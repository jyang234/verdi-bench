"""EVAL-4 AC-5 — timeout as outcome; no silent retry; infra rerun = new trial."""

from __future__ import annotations

from harness.adapters.base import Outcome
from harness.ledger.query import find_events
from harness.plan.interleave import Trial
from harness.run.engines.fake import FakeEngine
from harness.run.interleave import schedule
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.builders import fixed_ctx


def _arm(name="A"):
    return Arm(name=name, platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def test_ac5_timeout_outcome(tmp_path):
    task = Task(id="t", prompt="p", fake_behavior={"outcome": "timeout", "exit_status": 124})
    rec = run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=FakeEngine()))
    assert rec.outcome == Outcome.timeout  # timeout is data, not an exception


def test_ac5_infra_rerun_new_trial(tmp_path):
    # first attempt infra-fails, then succeeds; each attempt has a fresh id
    calls = {"n": 0}

    class FlakyInfraEngine(FakeEngine):
        def run(self, request):
            calls["n"] += 1
            if calls["n"] == 1:
                request.fake_behavior = {"outcome": "infra_failed", "infra_reason": "oom"}
            else:
                request.fake_behavior = {"outcome": "completed", "native_log": {}}
            return super().run(request)

    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p")}
    order = [Trial(task_id="t", arm="A", repetition=0)]
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson", ctx=fixed_ctx(),
        config=RunConfig(engine=FlakyInfraEngine()), cost_ceiling=100.0,
    )
    infra = find_events(tmp_path / "l.ndjson", "trial_infra_failed")
    trials = find_events(tmp_path / "l.ndjson", "trial")
    assert len(infra) == 1
    assert len(trials) == 1
    # the infra-failed trial id and the completed trial id are DIFFERENT
    assert infra[0]["trial_id"] != trials[0]["trial_record"]["trial_id"]
    assert res.infra_failures == 1


def test_ac5_no_silent_retry(tmp_path):
    """A completed trial is never silently re-run; exactly one trial event."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {}})}
    order = [Trial(task_id="t", arm="A", repetition=0)]
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson", ctx=fixed_ctx(),
        config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
    )
    assert len(find_events(tmp_path / "l.ndjson", "trial")) == 1


def test_ac5_infra_exhaustion_no_trial_event(tmp_path):
    """Persistent infra failure ⇒ only infra events, never a trial event."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"outcome": "infra_failed"})}
    order = [Trial(task_id="t", arm="A", repetition=0)]
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson", ctx=fixed_ctx(),
        config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0, max_infra_retries=2,
    )
    assert len(find_events(tmp_path / "l.ndjson", "trial")) == 0
    assert len(find_events(tmp_path / "l.ndjson", "trial_infra_failed")) == 3  # 1 + 2 retries
