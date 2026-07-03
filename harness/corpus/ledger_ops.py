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
from .registry import CalibrationSubset, CorpusManifest


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
