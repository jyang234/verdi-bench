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
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..judge.assemble import comparison_id_for
from ..ledger import events
from ..ledger.query import read_events
from ..run.flight_recorder import resolve_flight_recorder
from ..run.trajectory import resolve_trajectory


def trial_detail(experiment_dir, trial_id: str) -> Optional[dict]:
    experiment_dir = Path(experiment_dir)
    ledger_path = experiment_dir / "ledger.ndjson"

    record: Optional[dict] = None
    trajectory_sha = None
    flight_recorder_sha = None
    grades: list[dict] = []
    cant_grades: list[dict] = []
    verdicts: list[dict] = []
    flags: list[dict] = []
    metrics: Optional[dict] = None
    quarantine: Optional[dict] = None

    evs = read_events(ledger_path)
    for ev in evs:
        kind = ev.get("event")
        if kind == events.TRIAL:
            rec = ev.get("trial_record") or {}
            if rec.get("trial_id") == trial_id:
                record = rec
                trajectory_sha = ev.get("trajectory_sha")
                flight_recorder_sha = ev.get("flight_recorder_sha")
        elif kind == events.GRADE and ev.get("trial_id") == trial_id:
            grades.append(
                {
                    "task_sha": ev.get("task_sha"),
                    "assertions": ev.get("assertions"),
                    "binary_score": ev.get("binary_score"),
                    "fractional_score": ev.get("fractional_score"),
                    "grader": ev.get("grader"),
                    "override_of": ev.get("override_of"),
                    "ts": (ev.get("provenance") or {}).get("ts"),
                }
            )
        elif kind == events.CANT_GRADE and ev.get("trial_id") == trial_id:
            cant_grades.append(
                {
                    "reason": ev.get("reason"),
                    "override_of": ev.get("override_of"),
                    "ts": (ev.get("provenance") or {}).get("ts"),
                }
            )
        elif kind == events.FORENSIC_QUARANTINE:
            fq = ev.get("forensic_quarantine") or {}
            if fq.get("trial_id") == trial_id:
                quarantine = {"reason": fq.get("reason")}

    if record is None:
        return None

    # Judge verdicts join on the deterministic comparison id of this trial's
    # (task, repetition) cell — exactly what the judge stamped [D-P4-1].
    cmp_id = comparison_id_for(record.get("task_id"), record.get("repetition", 0))
    for ev in evs:
        if ev.get("event") == events.JUDGE_VERDICT:
            v = ev.get("verdict") or {}
            if v.get("comparison_id") == cmp_id:
                verdicts.append(v)

    # Forensics: the LATEST report's view of this trial (latest-wins, the
    # report.py precedent); flags carry their trial id, metrics key by it.
    reports = [ev for ev in evs if ev.get("event") == events.FORENSICS_REPORT]
    if reports:
        fr = reports[-1].get("forensics_report") or {}
        metrics = (fr.get("metrics") or {}).get(trial_id)
        flags = [f for f in (fr.get("flags") or []) if f.get("trial_id") == trial_id]

    status, trajectory = resolve_trajectory(record.get("artifacts_path"), trajectory_sha)
    steps = (
        [s.model_dump(mode="json") for s in trajectory.steps]
        if trajectory is not None
        else None
    )
    fr_status, fr_record = resolve_flight_recorder(
        record.get("artifacts_path"), flight_recorder_sha
    )
    fr_entries = (
        [e.model_dump(mode="json") for e in fr_record.entries]
        if fr_record is not None
        else None
    )

    rec_flags = record.get("flags") or {}
    return {
        "trial_id": trial_id,
        "record": record,
        "comparison_id": cmp_id,
        "trajectory": {"status": status, "steps": steps},
        # operator-tier reasoning with v3 linkage, for the process view; the
        # closed status vocabulary mirrors the trajectory's [EVAL-24]
        "flight_recorder": {"status": fr_status, "entries": fr_entries},
        "grade": {
            "grades": grades,
            "cant_grades": cant_grades,
            "binary_score": grades[-1]["binary_score"] if grades else None,
        },
        "verdicts": verdicts,
        "forensics": {"metrics": metrics, "flags": flags},
        "quarantine": quarantine,
        "egress": {
            "violation": rec_flags.get("egress_violation"),
            "attempts": rec_flags.get("egress_attempts"),
        },
    }
