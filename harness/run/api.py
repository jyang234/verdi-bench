"""``run`` stage API [refactor 02 §3].

The importable entry points behind ``bench run`` and ``bench control-cache
export`` [EVAL-4 §M6]: assert the lock, resolve tasks, derive the interleave from
the locked seed, and execute the schedule producing chained trial events and
redacted artifacts. The typer verbs (``harness/run/cli.py``) are thin shells that
map the enumerated refusals to exit codes and echo the counts.

Defaults to the fake engine (fast, hermetic-by-fiat); the Harbor engine is
selected with ``engine="harbor"`` and requires local Docker.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Optional

from ..plan.interleave import derive_schedule, enumerate_trials
from .types import OtlpConfig, ProxyConfig, RunConfig, Task


class NoTasksError(RuntimeError):
    """The experiment dir has no ``tasks.yaml`` to run [EVAL-8 stand-in]."""


class CorpusManifestMismatchError(RuntimeError):
    """A scheduled task is absent from the supplied corpus manifest — tasks.yaml
    and the manifest disagree, so scheduling fails closed [CO-2/D-P4-2]."""


class OtlpCoherenceError(RuntimeError):
    """An arm declares ``platform: otlp`` but the run configures no OTLP collector
    [refactor 10 §1, D-10-1].

    The span-derived trajectory source is pre-registered per arm; a run that cannot
    capture spans for such an arm would fail every one of its trials at capture time
    with a missing artifact. This is a run-preflight refusal — the platform capability
    check lives at plan/lock, but collector *coherence* needs the run config, which
    the locked spec cannot see, so it is validated here before any trial spends,
    naming both settings rather than surfacing mid-run."""


def _assert_otlp_coherence(arms, run_otlp) -> None:
    """Refuse a run whose ``platform: otlp`` arm has no collector configured, before
    any trial executes [refactor 10 §1, D-10-1]. A no-op when no arm is otlp."""
    otlp_arms = [a.name for a in arms if a.platform == "otlp"]
    if otlp_arms and run_otlp is None:
        raise OtlpCoherenceError(
            f"arm(s) {otlp_arms} declare platform: otlp but the run configures no OTLP "
            "collector (run.config.yaml 'otlp.endpoint' or 'otlp.managed: true') — the "
            "span-derived trajectory has no source. Configure a collector or use a "
            "log-reading platform [refactor 10 §1, D-10-1]."
        )


@dataclass(frozen=True)
class RunOutcome:
    """What ``bench run`` computed, for the shell to render in order.

    ``reused_arm``/``reused_cells`` describe an imported control bundle (echoed
    before the schedule summary); ``quarantine_error`` carries a schedule refusal
    (a scheduled task was quarantined) the shell reports before the summary — so
    the reuse notice still precedes it, exactly as the inline body ordered them.
    """

    n_trials: int
    infra_failures: int
    stopped_cost_ceiling: bool
    aborted_proxy_unavailable: bool
    reused_arm: str | None = None
    reused_cells: int | None = None
    quarantine_error: str | None = None


@dataclass(frozen=True)
class ControlExportOutcome:
    """What ``bench control-cache export`` wrote: cell count + bundle sha."""

    n_cells: int
    bundle_sha256: str


def _task_from_dict(t: dict, task_sha: str) -> Task:
    return Task(
        id=t["id"],
        prompt=t.get("prompt", ""),
        image=t.get("image", Task.__dataclass_fields__["image"].default),
        timeout_s=t.get("timeout_s"),
        holdout_canaries=t.get("holdout_canaries", []),
        fake_behavior=t.get("fake_behavior", {}),
        task_sha=task_sha,
        # EnvironmentSpec [refactor 03 §5, A3]: read the lenient dict (read side
        # stays untyped, A9); the raw bytes are already sha-covered by the lock.
        files=t.get("files", {}),
        env=t.get("env", {}),
        extra_hosts=t.get("extra_hosts", []),
    )


@contextmanager
def _managed_proxy(settings, engine: str, exp_dir: Path) -> Iterator[Optional[ProxyConfig]]:
    """Yield the ProxyConfig the schedule runs under, standing up the managed
    metering proxy when opted in [refactor 04 §1].

    A plain passthrough of ``settings.proxy`` unless ``proxy.managed`` is set and
    the engine manages real infrastructure (its ``manages_real_infra`` declaration
    [refactor 11 §G5c]) — the fake engine is hermetic-by-fiat and needs no docker,
    so a managed proxy would be pointless and would break its no-daemon guarantee.
    When active, it stands the proxy up (MeteringProxy refuses loudly if docker is
    unavailable), injects its url + log_path onto the spec-derived ProxyConfig
    (keeping the allowlist + infra_hosts), and always tears it down on exit.
    ``log_path`` defaults under the experiment dir.
    """
    from .engines import manages_real_infra

    if not settings.proxy_managed or not manages_real_infra(engine):
        yield settings.proxy
        return
    from ..hermetic.metering import MeteringProxy

    base = settings.proxy
    allow = list(base.allowlist) if base is not None else []
    log_path = (
        Path(base.log_path) if (base is not None and base.log_path)
        else exp_dir / "metering" / "verdi.jsonl"
    )
    with MeteringProxy.managed(allow, log_path=log_path) as managed_cfg:
        if base is None:
            yield managed_cfg
        else:
            yield replace(base, proxy_url=managed_cfg.proxy_url, log_path=managed_cfg.log_path)


@contextmanager
def _managed_collector(settings, engine: str, exp_dir: Path) -> Iterator[Optional[OtlpConfig]]:
    """Yield the OtlpConfig the schedule runs under, standing up the managed OTLP
    trace collector when opted in [refactor 09 §3/§4].

    A plain passthrough of ``settings.otlp`` unless ``otlp.managed`` is set and the
    engine manages real infrastructure (its ``manages_real_infra`` declaration
    [refactor 11 §G5c]) — the fake engine is hermetic-by-fiat and needs no docker,
    so a managed collector would be pointless and break its no-daemon guarantee.
    When active, it stands the collector up (TraceCollector refuses loudly if docker
    is unavailable), builds an OtlpConfig from its endpoint + log_path, and always
    tears it down on exit — deleting the raw envelope log per D-09-1. ``log_path``
    defaults under the experiment dir.
    """
    from .engines import manages_real_infra

    if not settings.otlp_managed or not manages_real_infra(engine):
        yield settings.otlp
        return
    from ..hermetic.tracing import TraceCollector

    log_path = exp_dir / "otlp" / "otlp.jsonl"
    with TraceCollector.managed(log_path=log_path) as cfg:
        yield OtlpConfig(endpoint=cfg.endpoint, log_path=cfg.log_path)


def run_experiment(
    exp_dir: Path,
    *,
    engine: str = "fake",
    corpus_manifest: Path | None = None,
    actor: str | None = None,
    reuse_control: Path | None = None,
) -> RunOutcome:
    """Execute the locked experiment's interleaved trials [EVAL-4 §M6].

    Raises the pre-schedule refusals the CLI maps — ``NoTasksError`` (→ bad
    parameter), ``TaskCommitmentError``/``CorpusManifestMismatchError``/
    ``ControlReuseError``/``ControlBundleError``/``ActorResolutionError`` (exit 2)
    — and reports a schedule-time quarantine refusal and the proxy abort as
    :class:`RunOutcome` fields so the shell can echo them in the body's order.
    """
    from ..corpus.commit import (
        assert_task_commitment,
        load_task_dicts,
        task_content_sha,
    )
    from ..corpus.registry import CorpusManifest
    from ..grade.baseline import load_quarantine
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from ..plan.lock import assert_lock
    from .engines import get_engine
    from .heartbeat import HEARTBEAT_FILENAME
    from .interleave import QuarantinedTaskError, schedule
    from .reuse import reused_arms
    from .settings import load_run_settings

    exp_dir = Path(exp_dir)
    spec_path = exp_dir / "experiment.yaml"
    ledger_path = exp_dir / "ledger.ndjson"
    _lock = assert_lock(spec_path, ledger_path)
    lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read

    task_dicts = load_task_dicts(exp_dir)
    if not task_dicts:
        raise NoTasksError(f"no tasks.yaml in {exp_dir}")
    # PL-7/D-6: refuse tasks that were swapped after the lock.
    assert_task_commitment(
        lock_event, task_dicts,
        corpus_id=spec.corpus.id, semver=spec.corpus.version,
    )
    tasks = [_task_from_dict(t, task_content_sha(t)) for t in task_dicts]
    task_map = {t.id: t for t in tasks}
    arm_map = {a.name: a for a in spec.arms}
    # RN-5: honor the flake quarantine — a quarantined task version (its clean
    # baseline never established) must not be scheduled [EVAL-5, D-2].
    quarantine = load_quarantine(ledger_path)

    # CO-2 / D-P4-2: when a corpus manifest is supplied, gate scheduling on
    # is_schedulable so pending/quarantined tasks don't run. Fail closed on drift:
    # every scheduled task must exist in the manifest, else the two sources
    # disagree.
    schedulable = None
    if corpus_manifest is not None:
        manifest = CorpusManifest.load(corpus_manifest)
        missing = [t.id for t in tasks if manifest.task(t.id) is None]
        if missing:
            raise CorpusManifestMismatchError(
                f"tasks {sorted(missing)} are not in corpus manifest "
                f"{manifest.corpus_id!r}; tasks.yaml and the manifest disagree "
                "[fail-closed, D-P4-2]"
            )
        schedulable = {t.id for t in tasks if manifest.is_schedulable(t.id)}

    trials = enumerate_trials(
        [t.id for t in tasks], [a.name for a in spec.arms], spec.repetitions
    )
    order = derive_schedule(spec.seed, trials)

    eng = get_engine(engine)
    # Operational config (proxy, quotas, provider keys) from run.config.yaml + env
    # — NOT from the sha-locked spec or the ledger [RN-13, D-9, AC-8]. Exception
    # [EVAL-20 AC-6]: a spec that pre-registers egress hosts derives the proxy
    # allowlist from those locked bytes.
    # A3: a task's extra_hosts extend the spec-derived proxy allowlist for all arms
    # (harness/run/egress.py); union them from the same locked task dicts.
    from .egress import task_extra_hosts

    settings = load_run_settings(
        exp_dir, spec=spec, task_extra_hosts=task_extra_hosts(task_dicts)
    )
    resolved_actor = resolve_actor(actor)
    ctx = EventContext(experiment_id=exp_dir.name, actor=resolved_actor)

    # Managed sidecars (opt-in): when run.config.yaml sets proxy.managed [refactor
    # 04 §1] and/or otlp.managed [refactor 09 §3], stand the metering proxy and/or
    # OTLP trace collector up around the whole schedule and tear them down after —
    # injecting their own url/endpoint + log_path onto the config the trials use. A
    # no-op passthrough (and no docker requirement) otherwise.
    with _managed_proxy(settings, engine, exp_dir) as run_proxy, _managed_collector(
        settings, engine, exp_dir
    ) as run_otlp:
        config = RunConfig(
            engine=eng,
            proxy=run_proxy,
            otlp=run_otlp,  # refactor 09 §4: in-trial OTLP capture (None = off)
            quotas=settings.quotas,
            provider_keys=settings.provider_keys,
            provider_key_names_by_arm=settings.provider_key_names_by_arm,  # PRA-M2
        )

        # refactor 10 §1 / D-10-1: an otlp arm with no collector configured has no
        # span source — refuse here (run preflight, before any trial spends) rather
        # than fail every otlp trial mid-run with a missing artifact.
        _assert_otlp_coherence(spec.arms, run_otlp)

        # Operational reuse surface: reuse_control arg, or a reuse_control.bundle
        # key in run.config.yaml — already parsed+resolved by load_run_settings [04 §4].
        if reuse_control is None:
            reuse_control = settings.reuse_control_bundle

        # Control reuse [control-reuse plan]: import the bundle's control-arm data
        # under the reused_* kinds (preflight refuses on any fingerprint drift), then
        # drop that arm's cells from the schedule.
        reused_arm_name: str | None = None
        reused_cells: int | None = None
        if reuse_control is not None:
            from .reuse import import_bundle, load_bundle

            bundle = load_bundle(reuse_control)
            reused_arm_name = import_bundle(
                exp_dir, bundle, ctx, engine=engine, spec=spec, settings=settings,
            )
            reused_cells = len(bundle["cells"])

        # Drop EVERY arm already imported as a reused control from the schedule —
        # read from the LEDGER, not just this invocation's flag [control-reuse].
        _reused = reused_arms(ledger_path)
        if _reused:
            order = [t for t in order if t.arm not in _reused]

        try:
            result = schedule(
                order,
                tasks=task_map,
                arms=arm_map,
                workspace_root=exp_dir / "workspaces",
                ledger_path=ledger_path,
                ctx=ctx,
                config=config,
                cost_ceiling=spec.cost_ceiling.amount,
                quarantined_tasks=quarantine,
                schedulable_tasks=schedulable,
                # Liveness sidecar for live observers [EVAL-13 AC-1]: operational
                # telemetry beside the ledger, never in it.
                heartbeat_path=exp_dir / HEARTBEAT_FILENAME,
            )
        except QuarantinedTaskError as e:
            return RunOutcome(
                n_trials=0, infra_failures=0, stopped_cost_ceiling=False,
                aborted_proxy_unavailable=False, reused_arm=reused_arm_name,
                reused_cells=reused_cells, quarantine_error=str(e),
            )
        return RunOutcome(
            n_trials=len(result.records),
            infra_failures=result.infra_failures,
            stopped_cost_ceiling=result.stopped_cost_ceiling,
            aborted_proxy_unavailable=result.aborted_proxy_unavailable,
            reused_arm=reused_arm_name,
            reused_cells=reused_cells,
        )


def export_control_bundle(exp_dir: Path, *, arm: str, out: Path) -> ControlExportOutcome:
    """Export a completed run's control arm as a reusable bundle [control-reuse].

    Snapshots each control trial's judged diff while the workspaces are still
    readable, so the bundle survives the source environment being reclaimed.
    Raises ``ControlBundleError``/``LockError`` (the CLI maps to exit 2).
    """
    from .reuse import build_bundle, write_bundle

    bundle = build_bundle(exp_dir, arm)
    write_bundle(bundle, out)
    return ControlExportOutcome(
        n_cells=len(bundle["cells"]), bundle_sha256=bundle["bundle_sha256"],
    )
