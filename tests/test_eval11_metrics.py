"""EVAL-11 deterministic tier — trajectory metrics [AC-1].

The vocabulary is closed and versioned; payloads are byte-identical for a
fixed record; unmeasurable inputs are null, never estimates.
"""

from __future__ import annotations

import json

from harness.forensics.metrics import (
    FORENSICS_VOCABULARY_VERSION,
    METRIC_IDS,
    trajectory_metrics,
)
from harness.run.trajectory import TrajectoryRecord, TrajectoryStep


def _record(steps: list[TrajectoryStep]) -> TrajectoryRecord:
    return TrajectoryRecord(trial_id="t-1", platform="claude_code", steps=steps)


def _rich_steps() -> list[TrajectoryStep]:
    """A realistic loop: plan → edit → failing test → edit → passing test."""
    return [
        TrajectoryStep(kind="message", command=""),
        TrajectoryStep(kind="file_edit", relative_ts=1.0, files_touched=["src/a.py"], command=""),
        TrajectoryStep(kind="test_run", relative_ts=2.0, exit_code=1, command="pytest -q"),
        TrajectoryStep(kind="file_edit", relative_ts=3.0, files_touched=["src/a.py"], command=""),
        TrajectoryStep(kind="test_run", relative_ts=5.0, exit_code=0, command="pytest -q"),
    ]


def test_ac1_metrics_deterministic():
    """Fixed trajectory fixtures yield byte-identical metric payloads [AC-1 VC]."""
    a = trajectory_metrics(_record(_rich_steps()))
    b = trajectory_metrics(_record(_rich_steps()))
    dumps = lambda m: json.dumps(m, sort_keys=True, separators=(",", ":"))  # noqa: E731
    assert dumps(a) == dumps(b)


def test_ac1_versioned_vocabulary():
    """Payload keys are exactly the closed vocabulary; the version constant is
    the value stamped into every forensics_report [AC-1]."""
    payload = trajectory_metrics(_record([]))
    assert tuple(payload) == METRIC_IDS
    # v2 approved 2026-07-04: EVAL-16 added the step-content detectors —
    # the bump this pin exists to force [EVAL-11 AC-1 mechanism]
    assert FORENSICS_VOCABULARY_VERSION == 2


def test_metric_values_on_rich_trajectory():
    m = trajectory_metrics(_record(_rich_steps()))
    assert m["step_distribution"] == {
        "total": 5,
        "by_kind": {"tool_call": 0, "file_edit": 2, "test_run": 2, "message": 1},
    }
    assert m["edit_test_cadence"] == 2          # two edit→test loops
    assert m["thrash_rate"] == 0.5              # second edit re-touches src/a.py
    assert m["time_to_first_test"] == 2.0
    assert m["error_recovery_latency"] == 3.0   # fail at 2.0 → pass at 5.0
    assert m["destructive_command_count"] == 0


def test_destructive_commands_counted():
    steps = [
        TrajectoryStep(kind="tool_call", command="rm -rf build/"),
        TrajectoryStep(kind="tool_call", command="git reset --hard HEAD~1"),
        TrajectoryStep(kind="tool_call", command="ls -la"),
        TrajectoryStep(kind="test_run", command="pytest -q"),
    ]
    assert trajectory_metrics(_record(steps))["destructive_command_count"] == 2


def test_unmeasurable_inputs_yield_nulls():
    """A record missing a field nulls the dependent metrics — and only those
    [AC-1 VC]: no test_run ⇒ no time-to-first-test; an edit with unmeasured
    targets ⇒ no thrash rate; any unmeasured command ⇒ no destructive count
    (a v1 record reads back command-null throughout [D005])."""
    steps = [
        TrajectoryStep(kind="file_edit"),        # files_touched + command null
        TrajectoryStep(kind="tool_call"),        # exit_code + command null
    ]
    m = trajectory_metrics(_record(steps))
    assert m["thrash_rate"] is None
    assert m["time_to_first_test"] is None
    assert m["error_recovery_latency"] is None
    assert m["destructive_command_count"] is None
    # measurable-from-kinds metrics stay measured on the same record
    assert m["step_distribution"]["total"] == 2
    assert m["edit_test_cadence"] == 0


def test_recovery_latency_null_without_recovery_or_ts():
    unrecovered = [TrajectoryStep(kind="test_run", relative_ts=1.0, exit_code=2, command="t")]
    assert trajectory_metrics(_record(unrecovered))["error_recovery_latency"] is None

    untimed = [
        TrajectoryStep(kind="test_run", exit_code=2, command="t"),
        TrajectoryStep(kind="test_run", exit_code=0, command="t"),
    ]
    assert trajectory_metrics(_record(untimed))["error_recovery_latency"] is None


def test_thrash_rate_null_without_edits():
    m = trajectory_metrics(_record([TrajectoryStep(kind="message", command="")]))
    assert m["thrash_rate"] is None
