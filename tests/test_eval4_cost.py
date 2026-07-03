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


def test_ac7_rerun_resumes_not_duplicates(tmp_path):
    """RN-1: a second schedule() on the same order+ledger resumes — already-run
    (task,arm,rep) cells are skipped, so no duplicate trials and no re-spend."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {"total_cost_usd": 0.10}})}
    ledger = tmp_path / "l.ndjson"
    order = [Trial(task_id="t", arm="A", repetition=r) for r in range(3)]
    kw = dict(
        tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
        ctx=fixed_ctx(), config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
    )
    first = schedule(order, **kw)
    assert len(first.records) == 3
    second = schedule(order, **kw)
    assert len(second.records) == 0  # every cell already executed
    assert len(find_events(ledger, "trial")) == 3  # not 6 — no duplicates


def test_ac7_rerun_after_ceiling_stop_adds_no_trials(tmp_path):
    """RN-1: after a ceiling stop, a re-run rebuilds prior spend (already at/over
    the pre-registered ceiling) and starts nothing — the ceiling is not reset."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {"total_cost_usd": 0.40}})}
    ledger = tmp_path / "l.ndjson"
    order = [Trial(task_id="t", arm="A", repetition=r) for r in range(6)]
    kw = dict(
        tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
        ctx=fixed_ctx(), config=RunConfig(engine=FakeEngine()), cost_ceiling=1.00,
    )
    first = schedule(order, **kw)
    ran = len(first.records)
    assert first.stopped_cost_ceiling is True and ran < 6
    second = schedule(order, **kw)
    assert len(second.records) == 0  # over budget already — nothing new starts
    assert len(find_events(ledger, "trial")) == ran  # no duplicates


def test_ac7_proxy_cost_enforced_when_telemetry_null(tmp_path):
    """RN-2: an arm that can't self-report cost (telemetry cost null) still counts
    against the ceiling via the proxy-metered figure — enforcement only, the
    record's telemetry.cost stays null (D004: nulls are never imputed)."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {}, "proxy_metered_cost": 0.60})}
    ledger = tmp_path / "l.ndjson"
    order = [Trial(task_id="t", arm="A", repetition=r) for r in range(4)]
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
        ctx=fixed_ctx(), config=RunConfig(engine=FakeEngine()), cost_ceiling=1.00,
    )
    assert res.stopped_cost_ceiling is True  # 0.60 * 2 >= 1.00 stops before all 4
    assert len(res.records) < 4
    assert all(r.telemetry.cost is None for r in res.records)  # not imputed


def test_ac7_infra_failed_attempts_count_against_ceiling(tmp_path):
    """RN-3: spend from infra-failed attempts accumulates and the guard is checked
    inside the infra-rerun loop, so costly-but-failing attempts can't burn the
    whole retry budget past the ceiling."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={
        "native_log": {}, "outcome": "infra_failed", "proxy_metered_cost": 0.40})}
    ledger = tmp_path / "l.ndjson"
    order = [Trial(task_id="t", arm="A", repetition=0)]
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
        ctx=fixed_ctx(), config=RunConfig(engine=FakeEngine()), cost_ceiling=1.00,
        max_infra_retries=10,
    )
    # with a fresh per-attempt guard the loop would retry 11x (burning 4.40);
    # with the guard checked inside, it stops after 3 attempts (0.40*3 = 1.20).
    assert len(find_events(ledger, "trial_infra_failed")) == 3
    assert res.stopped_cost_ceiling is True
