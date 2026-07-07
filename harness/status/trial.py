"""Per-trial drill-down join [EVAL-14 AC-2].

One pure read assembling everything the instrument knows about a single
trial: the ledgered record, its sha-verified trajectory (status always
stated, steps only when ``verified``), its sha-verified flight recorder
[flight-recorder charter] (status always stated, entries only when
``verified`` — operator tier, same as the compare screen; never the judge
packet or the fence), grade/cant_grade history with per-assertion detail,
the judge verdicts of its (task, repetition) comparison — joined by the same
deterministic ``comparison_id`` the judge stamps, never guessed — forensic
metrics/flags naming it, any quarantine disposition, and its egress record.
Nulls stay null end-to-end [EVAL-4-D004]; an unknown trial id returns
``None`` (the serve layer's 404).

The six-correlation join now lives on :meth:`LedgerView.trial_story`
([refactor 06 §1]); this is the operator-tier presentation adapter over it.
Callers holding a ``LedgerView`` over the same ledger may pass it as ``view``
so a batch drill-down (e.g. the static bundle) parses the ledger once instead
of once per trial.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..ledger.view import LedgerView


def trial_detail(
    experiment_dir, trial_id: str, *, view: Optional[LedgerView] = None
) -> Optional[dict]:
    if view is None:
        view = LedgerView(Path(experiment_dir) / "ledger.ndjson")
    story = view.trial_story(trial_id)
    if story is None:
        return None

    rec_flags = story.record.get("flags") or {}
    return {
        "trial_id": trial_id,
        "record": story.record,
        "comparison_id": story.comparison_id,
        "trajectory": {"status": story.trajectory_status, "steps": story.trajectory_steps},
        # operator-tier reasoning with v3 linkage, for the process view; the
        # closed status vocabulary mirrors the trajectory's [EVAL-24]
        "flight_recorder": {
            "status": story.flight_recorder_status,
            "entries": story.flight_recorder_entries,
        },
        "grade": {
            "grades": story.grades,
            "cant_grades": story.cant_grades,
            "binary_score": story.grades[-1]["binary_score"] if story.grades else None,
        },
        "verdicts": story.verdicts,
        "forensics": {"metrics": story.forensics_metrics, "flags": story.forensics_flags},
        "quarantine": story.quarantine,
        "egress": {
            "violation": rec_flags.get("egress_violation"),
            "attempts": rec_flags.get("egress_attempts"),
        },
    }
