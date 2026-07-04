"""Per-trial trajectory timelines [EVAL-12 AC-6].

``trial_timeline`` reads trial events and their trajectory artifacts into
per-task, per-arm rows for the dossier's analyst/auditor layers. Coverage is
explicit data: every trial carries a trajectory *status* — ``verified`` (bytes
match the ledgered sha), ``absent`` (pre-EVAL-12 trial or an engine that
honestly produced none), ``missing_artifact``, ``sha_mismatch``, or
``corrupt`` — so partial coverage is disclosed, never silent. Unmeasured
telemetry stays ``None`` here; the renderer phrases it "not measured", never
zero [EVAL-4-D004].
"""

from __future__ import annotations

from pathlib import Path

from ..ledger import events
from ..ledger.query import find_events
from ..run.trajectory import (
    TRAJECTORY_FILENAME,
    TrajectoryCorruptError,
    load_trajectory,
    trajectory_sha256,
)

# Closed trajectory-coverage vocabulary; only `verified` yields renderable steps.
TRAJECTORY_STATUSES = ("verified", "absent", "missing_artifact", "sha_mismatch", "corrupt")


def _trajectory_for(rec: dict, ledgered_sha) -> tuple[str, list | None]:
    """Resolve one trial's trajectory to ``(status, steps-or-None)``.

    Steps render only when the artifact bytes hash to the ledgered sha — an
    edited or unhashed trajectory is a coverage gap with a named reason, never
    silently treated as evidence.
    """
    if ledgered_sha is None:
        return "absent", None
    artifacts_path = rec.get("artifacts_path")
    if not artifacts_path:
        return "missing_artifact", None
    path = Path(artifacts_path) / TRAJECTORY_FILENAME
    if not path.exists():
        return "missing_artifact", None
    raw = path.read_bytes()
    if trajectory_sha256(raw) != ledgered_sha:
        return "sha_mismatch", None
    try:
        record = load_trajectory(path)
    except TrajectoryCorruptError:
        return "corrupt", None
    return "verified", [s.model_dump(mode="json") for s in record.steps]


def trial_timeline(ledger_path) -> dict[str, dict[str, list[dict]]]:
    """``task_id -> arm -> [trial rows]`` in deterministic order.

    Each row: ``{trial_id, repetition, outcome, wall_time_s, telemetry_nulls,
    trajectory_status, steps}``; ``steps`` is a list of step dicts for a
    ``verified`` trajectory and ``None`` otherwise. Rows are sorted by
    ``(repetition, trial_id)`` so a render is a pure function of the ledger.
    """
    acc: dict[str, dict[str, list[dict]]] = {}
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        status, steps = _trajectory_for(rec, ev.get("trajectory_sha"))
        row = {
            "trial_id": rec["trial_id"],
            "repetition": rec.get("repetition", 0),
            "outcome": rec.get("outcome"),
            "wall_time_s": (rec.get("telemetry") or {}).get("wall_time_s"),
            "telemetry_nulls": list(rec.get("telemetry_nulls") or []),
            "trajectory_status": status,
            "steps": steps,
        }
        acc.setdefault(rec["task_id"], {}).setdefault(rec["arm"], []).append(row)
    return {
        task_id: {
            arm: sorted(rows, key=lambda r: (r["repetition"], r["trial_id"]))
            for arm, rows in sorted(arms.items())
        }
        for task_id, arms in sorted(acc.items())
    }
