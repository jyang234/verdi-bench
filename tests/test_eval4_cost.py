"""EVAL-4 AC-7 — cost ceiling stops the run and is ledgered."""

from __future__ import annotations

from harness.ledger.query import find_events
from harness.plan.interleave import Trial
from harness.run.engines.fake import FakeEngine
from harness.run.interleave import schedule
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.builders import fixed_ctx


def _arm():
    return Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def test_ac7_ceiling_stops_and_ledgered(tmp_path):
    # each trial costs 0.40; ceiling 1.00 ⇒ after 3 trials (1.20) the guard stops
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {"total_cost_usd": 0.40}})}
    order = [Trial(task_id="t", arm="A", repetition=r) for r in range(6)]
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson", ctx=fixed_ctx(),
        config=RunConfig(engine=FakeEngine()), cost_ceiling=1.00,
    )
    assert res.stopped_cost_ceiling is True
    # stopped after accumulated >= ceiling; fewer than all 6 ran
    assert len(res.records) < 6
    stops = find_events(tmp_path / "l.ndjson", "run_stopped_cost_ceiling")
    assert len(stops) == 1
    assert stops[0]["accumulated_cost"] >= 1.00
    assert stops[0]["ceiling"] == 1.00


def test_ac7_no_stop_under_ceiling(tmp_path):
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {"total_cost_usd": 0.01}})}
    order = [Trial(task_id="t", arm="A", repetition=r) for r in range(3)]
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson", ctx=fixed_ctx(),
        config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
    )
    assert res.stopped_cost_ceiling is False
    assert find_events(tmp_path / "l.ndjson", "run_stopped_cost_ceiling") == []
    assert len(res.records) == 3
