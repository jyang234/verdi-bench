"""``bench run`` [EVAL-4 §M6].

Asserts the experiment lock first, resolves tasks, derives the interleave from
the locked seed, and executes the schedule producing chained trial events and
redacted artifacts. Defaults to the fake engine (fast, hermetic-by-fiat); the
Harbor engine is selected with ``--engine harbor`` and requires local Docker.

Task resolution: EVAL-8 owns corpus import; until it lands, ``bench run`` reads a
``tasks.yaml`` in the experiment dir as the task source (a documented stand-in).
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..ledger.actor import ActorResolutionError, resolve_actor
from ..plan.interleave import derive_schedule, enumerate_trials
from .types import RunConfig, Task


def _task_from_dict(t: dict, task_sha: str) -> Task:
    return Task(
        id=t["id"],
        prompt=t.get("prompt", ""),
        image=t.get("image", Task.__dataclass_fields__["image"].default),
        timeout_s=t.get("timeout_s"),
        holdout_canaries=t.get("holdout_canaries", []),
        fake_behavior=t.get("fake_behavior", {}),
        task_sha=task_sha,
    )


def register(app: typer.Typer) -> None:
    @app.command()
    def run(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        engine: str = typer.Option("fake", "--engine", help="fake | harbor"),
        corpus_manifest: Path = typer.Option(
            None, "--corpus-manifest", help="Manifest gating schedulability (is_schedulable) [CO-2]"
        ),
        actor: str = typer.Option(
            None, "--actor", help="Actor recorded on the trial events [GR-12]"
        ),
        reuse_control: Path = typer.Option(
            None,
            "--reuse-control",
            help="Path to a control bundle to reuse instead of running the "
            "control arm (exploratory-only; preflight refuses on any drift)",
        ),
    ) -> None:
        """Execute the locked experiment's interleaved trials."""
        from ..corpus.commit import (
            TaskCommitmentError,
            assert_task_commitment,
            load_task_dicts,
            task_content_sha,
        )
        from ..corpus.registry import CorpusManifest
        from ..grade.baseline import load_quarantine
        from ..ledger.events import EventContext
        from ..plan.lock import assert_lock
        from .heartbeat import HEARTBEAT_FILENAME
        from .interleave import QuarantinedTaskError, schedule

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        _lock = assert_lock(spec_path, ledger_path)
        lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read

        task_dicts = load_task_dicts(experiment_dir)
        if not task_dicts:
            raise typer.BadParameter(f"no tasks.yaml in {experiment_dir}")
        # PL-7/D-6: refuse tasks that were swapped after the lock.
        try:
            assert_task_commitment(
                lock_event, task_dicts,
                corpus_id=spec.corpus.id, semver=spec.corpus.version,
            )
        except TaskCommitmentError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        tasks = [_task_from_dict(t, task_content_sha(t)) for t in task_dicts]
        task_map = {t.id: t for t in tasks}
        arm_map = {a.name: a for a in spec.arms}
        # RN-5: honor the flake quarantine — a quarantined task version (its clean
        # baseline never established) must not be scheduled [EVAL-5, D-2].
        quarantine = load_quarantine(ledger_path)

        # CO-2 / D-P4-2: when a corpus manifest is supplied, gate scheduling on
        # is_schedulable so pending/quarantined tasks don't run. tasks.yaml +
        # task_commitment stay the integrity fence; the manifest is the
        # schedulability source. Fail closed on drift: every scheduled task must
        # exist in the manifest, else the two sources disagree.
        schedulable = None
        if corpus_manifest is not None:
            manifest = CorpusManifest.load(corpus_manifest)
            missing = [t.id for t in tasks if manifest.task(t.id) is None]
            if missing:
                typer.echo(
                    f"tasks {sorted(missing)} are not in corpus manifest "
                    f"{manifest.corpus_id!r}; tasks.yaml and the manifest disagree "
                    "[fail-closed, D-P4-2]", err=True,
                )
                raise typer.Exit(code=2)
            schedulable = {t.id for t in tasks if manifest.is_schedulable(t.id)}

        trials = enumerate_trials(
            [t.id for t in tasks], [a.name for a in spec.arms], spec.repetitions
        )
        order = derive_schedule(spec.seed, trials)

        from .engines import get_engine
        from .settings import load_run_settings

        eng = get_engine(engine)
        # Operational config (proxy, quotas, provider keys) from run.config.yaml +
        # env — NOT from the sha-locked spec or the ledger [RN-13, D-9, AC-8].
        # Exception [EVAL-20 AC-6]: a spec that pre-registers egress hosts
        # derives the proxy allowlist from those locked bytes.
        settings = load_run_settings(experiment_dir, spec=spec)
        config = RunConfig(
            engine=eng,
            proxy=settings.proxy,
            quotas=settings.quotas,
            provider_keys=settings.provider_keys,
            provider_key_names_by_arm=settings.provider_key_names_by_arm,  # PRA-M2
        )
        try:
            resolved_actor = resolve_actor(actor)
        except ActorResolutionError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        ctx = EventContext(experiment_id=experiment_dir.name, actor=resolved_actor)

        # Operational reuse surface: --reuse-control, or a reuse_control.bundle key
        # in run.config.yaml (operational config, never the sha-locked spec).
        if reuse_control is None:
            import yaml

            rc_path = experiment_dir / "run.config.yaml"
            if rc_path.exists():
                rc = (yaml.safe_load(rc_path.read_text(encoding="utf-8")) or {}).get("reuse_control")
                if isinstance(rc, dict) and rc.get("bundle"):
                    b = Path(rc["bundle"])
                    reuse_control = b if b.is_absolute() else experiment_dir / b

        # Control reuse [control-reuse plan]: import the bundle's control-arm data
        # under the reused_* kinds (preflight refuses on any fingerprint drift),
        # then drop that arm's cells from the schedule — they are supplied by the
        # bundle, not run. The official paired analysis then has no fresh control
        # to pair against (honest: reuse is exploratory, validation is a full run).
        if reuse_control is not None:
            from .control_reuse import ControlReuseError
            from .reuse import ControlBundleError, filter_reused_cells, import_bundle, load_bundle

            try:
                bundle = load_bundle(reuse_control)
                reused_arm = import_bundle(
                    experiment_dir, bundle, ctx, engine=engine, spec=spec, settings=settings,
                )
            except (ControlReuseError, ControlBundleError) as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(code=2)
            order = filter_reused_cells(order, reused_arm)
            typer.echo(f"reusing control arm {reused_arm!r} from bundle ({len(bundle['cells'])} cells)")

        try:
            result = schedule(
                order,
                tasks=task_map,
                arms=arm_map,
                workspace_root=experiment_dir / "workspaces",
                ledger_path=ledger_path,
                ctx=ctx,
                config=config,
                cost_ceiling=spec.cost_ceiling.amount,
                quarantined_tasks=quarantine,
                schedulable_tasks=schedulable,
                # Liveness sidecar for live observers [EVAL-13 AC-1]: operational
                # telemetry beside the ledger, never in it.
                heartbeat_path=experiment_dir / HEARTBEAT_FILENAME,
            )
        except QuarantinedTaskError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        typer.echo(
            f"ran {len(result.records)} trials "
            f"(infra_failures={result.infra_failures}, "
            f"stopped_cost_ceiling={result.stopped_cost_ceiling})"
        )
        if result.aborted_proxy_unavailable:
            # PRA-M9: a dead/misconfigured metering proxy aborted the run; exit
            # nonzero so the operator does not mistake a truncated run for a
            # complete one.
            typer.echo(
                "RUN ABORTED: the metering proxy is dead or misconfigured "
                "(proxy_log_missing); trials remaining were not run [PRA-M9]",
                err=True,
            )
            raise typer.Exit(code=2)

    cache_app = typer.Typer(
        help="Control-run reuse bundles [control-reuse plan].", no_args_is_help=True
    )
    app.add_typer(cache_app, name="control-cache")

    @cache_app.command("export")
    def control_cache_export(
        experiment_dir: Path = typer.Argument(
            ..., help="A completed source experiment directory to export from"
        ),
        arm: str = typer.Option(..., "--arm", help="The control arm to export"),
        out: Path = typer.Option(..., "--out", help="Bundle output path"),
    ) -> None:
        """Export a completed run's control arm as a reusable bundle.

        Snapshots each control trial's judged diff while the workspaces are still
        readable, so the bundle survives the source environment being reclaimed.
        """
        from ..plan.lock import LockError
        from .reuse import ControlBundleError, build_bundle, write_bundle

        try:
            bundle = build_bundle(experiment_dir, arm)
        except (ControlBundleError, LockError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        write_bundle(bundle, out)
        typer.echo(
            f"exported control bundle: arm {arm!r}, {len(bundle['cells'])} cell(s) "
            f"-> {out} (sha {bundle['bundle_sha256'][:12]}…)"
        )
