"""EVAL-4 AC-4 — interleave from seed; executed order ledgered."""

from __future__ import annotations

from harness.ledger.query import find_events
from harness.plan.interleave import derive_schedule, enumerate_trials
from harness.run.engines.fake import FakeEngine
from harness.run.interleave import schedule
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.builders import ctx_for


def _setup():
    arms = {
        "A": Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022"),
        "B": Arm(name="B", platform="codex", model="openai/gpt-4o-2024-08-06"),
    }
    tasks = {
        tid: Task(id=tid, prompt="p", fake_behavior={"native_log": {"total_cost_usd": 0.01}})
        for tid in ["t1", "t2"]
    }
    return arms, tasks


def _order(seed, tasks, arms, reps=2):
    trials = enumerate_trials(list(tasks), list(arms), reps)
    return derive_schedule(seed, trials)


def test_ac4_interleave_from_seed(tmp_path):
    arms, tasks = _setup()
    order = _order(1234, tasks, arms)
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson", ctx=ctx_for(tmp_path), config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    # the executed order matches the derived order (all completed, no failures)
    executed_keys = [(e["task_id"], e["arm"], e["repetition"]) for e in res.executed_order]
    derived_keys = [(t.task_id, t.arm, t.repetition) for t in order]
    assert executed_keys == derived_keys
    assert len(res.records) == len(order)


def test_rn15_unknown_arm_fails_cell_closed(tmp_path):
    """RN-15: a scheduled arm absent from ``arms`` fails THAT cell closed —
    trial_infra_failed(unknown_arm) — and the cell still lands in executed_order,
    rather than crashing the run with a bare KeyError."""
    from harness.plan.interleave import Trial

    arms, tasks = _setup()
    # a planned cell naming an arm that is not in the arms map
    order = [Trial(task_id="t1", arm="GHOST", repetition=0)]
    ledger = tmp_path / "l.ndjson"
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=ledger, ctx=ctx_for(tmp_path), config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    failed = find_events(ledger, "trial_infra_failed")
    assert len(failed) == 1 and failed[0]["reason"] == "unknown_arm"
    assert find_events(ledger, "trial") == []  # no real trial ran
    # the failed cell is still in the executed order (never skipped)
    assert [(e["task_id"], e["arm"]) for e in res.executed_order] == [("t1", "GHOST")]


def test_rn16_unwritable_secret_fails_cell_closed(tmp_path, monkeypatch):
    """RN-16: a detected-but-unredactable secret raises RedactionError, which
    fails the cell closed as trial_infra_failed(redaction_error) — a
    detected-but-unredacted secret never persists silently."""
    import harness.run.seam as seam_mod
    from harness.plan.interleave import Trial
    from harness.run.redact import RedactionError

    def boom(workspace, extra_patterns):
        raise RedactionError("found a secret but could not rewrite it")

    monkeypatch.setattr(seam_mod, "redact_artifacts", boom)

    arms, tasks = _setup()
    order = [Trial(task_id="t1", arm="A", repetition=0)]
    ledger = tmp_path / "l.ndjson"
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=ledger, ctx=ctx_for(tmp_path), config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    failed = find_events(ledger, "trial_infra_failed")
    assert failed and failed[-1]["reason"] == "redaction_error"
    assert find_events(ledger, "trial") == []  # the cell never became a real trial


def test_ac4_executed_order_ledgered(tmp_path):
    arms, tasks = _setup()
    order = _order(42, tasks, arms)
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson", ctx=ctx_for(tmp_path), config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    evs = find_events(tmp_path / "l.ndjson", "executed_order")
    assert len(evs) == 1
    assert len(evs[0]["order"]) == len(order)


def test_ac4_seed_changes_executed_order(tmp_path):
    arms, tasks = _setup()
    keys = []
    for seed in (1, 2):
        order = _order(seed, tasks, arms)
        keys.append([(t.task_id, t.arm, t.repetition) for t in order])
    assert keys[0] != keys[1]
