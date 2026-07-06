"""Ledgered corpus lifecycle operations [EVAL-8 §7.2, CO-4, CO-9].

The :class:`~harness.corpus.registry.CorpusManifest` model stays pure (in-memory
status/subset advance); the *ledgering* of calibration runs and subset draws is a
separate concern kept here, so calibration status and the seeded draw become
chain-anchored rather than only hand-editable manifest JSON.

Each function performs one ledgered operation — it appends exactly one event and
returns it. Production run-path/CLI wiring of ``ledger_calibration_run`` is Phase 4
(the run-path hook); ``ledger_subset_draw`` is wired into ``bench corpus subset``.
"""

from __future__ import annotations

from typing import Literal

from ..ledger import events
from ..ledger.events import EventContext
from .registry import CalibrationSubset, CorpusManifest, TaskEntry


class NoGradedTrialsError(RuntimeError):
    """Calibration was asked to derive variance from a ledger with no grades."""


def realized_calibration_run(ledger_path, *, rho: float, kind: str) -> dict:
    """The calibration ``run`` record derived from a completed run's realized
    grades [CO-4, refactor 07 §3].

    Groups the ledger's binary grades by task and returns the record
    :func:`ledger_calibration_run` ledgers and the manifest stores:
    ``{"p": <mean holdout pass rate>, "rho": <recorded assumption>,
    "n_tasks": ..., "kind": ...}``. Raises :class:`NoGradedTrialsError` when the
    ledger carries no gradeable trial. ``rho`` is a recorded assumption (full
    estimation is Phase 5). Pure over the ledger — this is the statistics the
    CLI/api no longer inline [thin CLI]."""
    from ..ledger.query import find_events

    trial_task = {
        ev["trial_record"]["trial_id"]: ev["trial_record"]["task_id"]
        for ev in find_events(ledger_path, events.TRIAL)
    }
    by_task: dict[str, list[float]] = {}
    for ev in find_events(ledger_path, events.GRADE):
        task_id = trial_task.get(ev["trial_id"])
        if task_id is None:
            continue
        by_task.setdefault(task_id, []).append(1.0 if ev["binary_score"] else 0.0)
    if not by_task:
        raise NoGradedTrialsError("no graded trials to calibrate from")
    all_scores = [s for xs in by_task.values() for s in xs]
    p = sum(all_scores) / len(all_scores)
    return {"p": round(p, 6), "rho": rho, "n_tasks": len(by_task), "kind": kind}


def ledger_calibration_run(
    ledger_path,
    ctx: EventContext,
    manifest: CorpusManifest,
    run: dict,
    *,
    kind: Literal["subset", "full"],
) -> dict:
    """Advance the manifest's calibration status and ledger the run [CO-4].

    Advances ``none → subset-validated → full-run-validated`` (the pure manifest
    method) and appends one ``calibration_run`` event carrying the resulting
    status, so the official-finding fence can bind to a chain-anchored status
    instead of trusting mutable JSON."""
    manifest.record_calibration_run(run, kind=kind)
    return events.record_calibration_run(
        ledger_path,
        ctx,
        corpus_id=manifest.corpus_id,
        semver=manifest.semver,
        kind=kind,
        run=run,
        status=manifest.calibration.status,
    )


def ledger_subset_draw(
    ledger_path,
    ctx: EventContext,
    manifest: CorpusManifest,
    subset: CalibrationSubset,
) -> dict:
    """Ledger a seeded stratified calibration-subset draw [CO-9]."""
    return events.record_subset_draw(
        ledger_path,
        ctx,
        corpus_id=manifest.corpus_id,
        semver=manifest.semver,
        seed=subset.seed,
        stratum_key=str(subset.strata.get("stratum_key", "")),
        task_ids=list(subset.task_ids),
        strata=subset.strata,
    )


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
def _prop_manifest() -> CorpusManifest:
    return CorpusManifest(
        corpus_id="prop", semver="1.0.0", kind="public",
        tasks=[
            TaskEntry(task_id=f"t{i}", sha=f"{i}".rjust(64, "0"), status="admitted",
                      metadata={"category": "io"})
            for i in range(4)
        ],
    )


def _calibration_run_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    d = Path(ctx_dir)
    ledger_calibration_run(
        d / "ledger.ndjson", EventContext(experiment_id="prop"),
        _prop_manifest(), {"anchor_delta": 0.01}, kind="subset",
    )


def _subset_draw_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from .stratify import calibration_subset

    d = Path(ctx_dir)
    manifest = _prop_manifest()
    subset = calibration_subset(manifest, seed=7, target_size=2, stratum_key="category")
    ledger_subset_draw(d / "ledger.ndjson", EventContext(experiment_id="prop"),
                       manifest, subset)


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("corpus-calibration-run", _calibration_run_entrypoint)
    register_entrypoint("corpus-subset-draw", _subset_draw_entrypoint)


_register()
