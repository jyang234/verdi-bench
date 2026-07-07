"""``forensics`` stage API [refactor 02 §3].

The importable entry points behind ``bench forensics scan|record|quarantine``
[EVAL-11]: scan every trial into exactly one ``forensics_report`` [AC-6], record
a human per-detector spot-check [AC-4, D006], and ledger the operator
quarantine disposition [D003, D007]. Each takes a resolved :class:`EventContext`
(the CLI owns actor→ctx resolution) and delegates to the scan core
(:mod:`harness.forensics.scan`); the typer verbs are thin shells.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..ledger.events import EventContext, record_forensic_spotcheck
from .scan import quarantine_trial, run_forensics


@dataclass(frozen=True)
class ForensicsScanOutcome:
    """The scan's coverage summary — exactly what the verb echoes."""

    covered: int
    trials: int
    n_flags: int
    n_gaps: int


def forensics_scan(
    experiment_dir, *, ctx: EventContext, review: bool = True, model: str | None = None
) -> ForensicsScanOutcome:
    """Scan every trial; append exactly one ``forensics_report`` event [AC-6].

    ``review`` runs the blinded advisory LLM pass (fails closed to CANT_REVIEW);
    ``model`` overrides the review provider model (default: the judge model)."""
    report = run_forensics(
        Path(experiment_dir), ctx=ctx, review=review, provider_model=model
    )
    cov = report["coverage"]
    return ForensicsScanOutcome(
        covered=cov["covered"], trials=cov["trials"],
        n_flags=len(report["flags"]), n_gaps=len(cov["gaps"]),
    )


def forensics_record(
    experiment_dir, *, ctx: EventContext, trial_id: str, labels: dict,
    stratum: str = "mandatory",
) -> None:
    """Record a human per-detector spot-check [AC-4, D006].

    ``labels`` is a ``{detector_id: bool}`` map already validated by the caller
    (the CLI names unknown ids/non-booleans before ledgering)."""
    record_forensic_spotcheck(
        Path(experiment_dir) / "ledger.ndjson", ctx,
        trial_id=trial_id, labels=labels, stratum=stratum,
    )


def quarantine(
    experiment_dir, *, ctx: EventContext, trial_id: str, reason: str
) -> None:
    """Ledger the operator disposition: exclude a trial, disclosed [D007].

    Raises ``UnknownTrialError`` (the CLI maps to exit 2) for a trial id the
    ledger has no record of — a ledgered exclusion that matched nothing would
    render as an exclusion that never happened."""
    quarantine_trial(Path(experiment_dir), ctx=ctx, trial_id=trial_id, reason=reason)
