"""Forensics scan core + operator dispositions [EVAL-11 §M4, AC-6, D006, D007].

``run_forensics`` is the thin composition [refactor 06 §5]: a
:class:`~harness.forensics.assembler.TrialEvidenceAssembler` turns the ledger and
on-disk artifacts into per-trial :class:`TrialEvidence` + coverage notes
(deterministic, provider-free), the Phase-4 detector registry runs over that
evidence, the blinded advisory review optionally runs on the spliced transcript
the assembler prepared, and one ``forensics_report`` event is appended — partial
coverage disclosed with a per-trial reason, never silent [AC-6].

``quarantine_trial`` is the D007 operator path; it refuses a trial id the ledger
does not know, because a ledgered exclusion that silently matched nothing would
render as an exclusion that never happened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..ledger.events import (
    EventContext,
    record_forensic_quarantine,
    record_forensics_report,
)
from .detectors import run_detectors
from .metrics import FORENSICS_VOCABULARY_VERSION


class UnknownTrialError(ValueError):
    """A disposition named a trial the ledger has no record of [D007]."""


def run_forensics(
    experiment_dir: Path,
    *,
    ctx: EventContext,
    review: bool = True,
    provider=None,
    provider_model: Optional[str] = None,
) -> dict:
    """The scan core: assemble → detect → (review) → one ledgered report."""
    from ..blind.core import arm_canaries
    from ..corpus.commit import load_task_dicts
    from ..ledger.view import LedgerView
    from ..plan.lock import assert_lock
    from .assembler import TrialEvidenceAssembler
    from .review import forensic_review

    experiment_dir = Path(experiment_dir)
    spec_path = experiment_dir / "experiment.yaml"
    ledger_path = experiment_dir / "ledger.ndjson"
    spec = assert_lock(spec_path, ledger_path).spec  # PRA-M1: no second spec read
    tasks = {t["id"]: t for t in load_task_dicts(experiment_dir)}
    canaries = arm_canaries(spec.arms)

    view = LedgerView(ledger_path)
    assembled = TrialEvidenceAssembler(
        view, experiment_dir, tasks, review=review
    ).assemble()

    flags: list[dict] = []
    reviews: dict[str, dict] = {}
    for at in assembled.trials:
        # the detector pass — the Phase-4 registry over the frozen evidence
        flags.extend(run_detectors(at.evidence))
        if review:
            # the advisory pass — the sole provider-touching phase, over the
            # transcript the assembler already spliced and byte-budgeted
            reviews[at.trial_id] = forensic_review(
                at.trial_id,
                at.review_transcript,
                canaries=canaries,
                provider=provider,
                provider_model=provider_model or spec.judge.model,
                max_reasoning_bytes=at.max_reasoning_bytes,
            ).model_dump(mode="json")

    report = {
        "vocabulary_version": FORENSICS_VOCABULARY_VERSION,
        "metrics": assembled.metrics,
        "flags": flags,
        "coverage": {
            "trials": assembled.n_trials,
            "covered": len(assembled.metrics),
            "gaps": assembled.gaps,
            # additive keys [EVAL-16 D002]: old readers ignore them, old
            # ledgers simply lack them, the report stays one event
            "detail_by_arm": {
                arm: assembled.detail_by_arm[arm] for arm in sorted(assembled.detail_by_arm)
            },
            "detail_gaps": assembled.detail_gaps,
            # F-H3 (additive): trials whose end-state evidence could not be
            # verified against the grade-time workspace commitment.
            "workspace_gaps": assembled.workspace_gaps,
        },
    }
    if review:
        report["reviews"] = reviews
    record_forensics_report(ledger_path, ctx, forensics_report=report)
    return report


def quarantine_trial(experiment_dir: Path, *, ctx: EventContext, trial_id: str, reason: str) -> dict:
    """Ledger the D007 operator disposition — refused for an unknown trial id.

    A quarantine that matches no trial would still render as '(excluded from
    comparisons)' while excluding nothing; validating here keeps the ledgered
    disclosure true [fail loudly]."""
    from ..ledger.view import LedgerView

    ledger_path = Path(experiment_dir) / "ledger.ndjson"
    known = {t.trial_id for t in LedgerView(ledger_path).trials()}
    if trial_id not in known:
        raise UnknownTrialError(
            f"cannot quarantine {trial_id!r}: no trial record with that id on "
            f"{ledger_path} ({len(known)} trial(s) known) — a quarantine that "
            "matches nothing would disclose an exclusion that never happened"
        )
    return record_forensic_quarantine(ledger_path, ctx, trial_id=trial_id, reason=reason)
