"""The ledgered ``forensics_report`` dict shape [refactor 06 §5, §9].

``build_forensics_report`` is the scan's final phase: it serializes the
deterministic :class:`~harness.forensics.assembler.AssembledEvidence` plus the
detector flags and (optional) advisory reviews into the exact dict that becomes
the hash-chained ``forensics_report`` payload. The shape is a FROZEN public seam
[CLAUDE.md: public seams are contracts] — its key set, nesting, per-field null
policy, and the additive-key discipline (old readers ignore new keys, old ledgers
simply lack them) never change without approval; the golden pin
(``tests/test_forensics_report_golden.py``) proves each commit keeps it identical.

Isolating the shape here keeps ``run_forensics`` a thin composition and gives the
one contract that everything downstream reads a single, obvious home.
"""

from __future__ import annotations

from typing import Optional

from .assembler import AssembledEvidence
from .metrics import FORENSICS_VOCABULARY_VERSION


def build_forensics_report(
    assembled: AssembledEvidence,
    flags: list[dict],
    reviews: Optional[dict[str, dict]],
) -> dict:
    """Assemble the exact ledgered ``forensics_report`` payload [refactor 06 §5].

    ``reviews`` is the advisory pass's per-trial map when the scan ran with
    ``review=True`` (possibly empty — a reviewed scan of nothing), or ``None``
    when it did not. A ``--no-review`` scan therefore omits the ``reviews`` key
    entirely, so the report renders as a SKIPPED advisory pass rather than a pass
    that ran and reviewed zero trials.
    """
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
    if reviews is not None:
        report["reviews"] = reviews
    return report
