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
from ..schema.experiment import ExperimentSpec
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
        from .interleave import QuarantinedTaskError, schedule

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        lock_event = assert_lock(spec_path, ledger_path)
        spec = ExperimentSpec.from_yaml(spec_path)

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
        settings = load_run_settings(experiment_dir)
        config = RunConfig(
            engine=eng,
            proxy=settings.proxy,
            quotas=settings.quotas,
            provider_keys=settings.provider_keys,
        )
        try:
            resolved_actor = resolve_actor(actor)
        except ActorResolutionError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        ctx = EventContext(experiment_id=experiment_dir.name, actor=resolved_actor)

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
            )
        except QuarantinedTaskError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        typer.echo(
            f"ran {len(result.records)} trials "
            f"(infra_failures={result.infra_failures}, "
            f"stopped_cost_ceiling={result.stopped_cost_ceiling})"
        )
