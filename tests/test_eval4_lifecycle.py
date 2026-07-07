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
from tests.fixtures.builders import ctx_for


def _arm(name="A"):
    return Arm(name=name, platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def test_ac5_explicit_zero_timeout_honored(tmp_path):
    # regression: task.timeout_s=0 must not be dropped by a falsy-or to the default
    from harness.run.engines.harbor import HarborEngine
    from tests.fixtures.run_fakes import FakeDockerRunner

    captured = {}

    class RecordingRunner(FakeDockerRunner):
        def run_container(self, cmd, timeout_s, env=None):
            captured["timeout"] = timeout_s
            return super().run_container(cmd, timeout_s, env)

    task = Task(id="t", prompt="p", timeout_s=0)
    run_trial(task, _arm(), tmp_path / "ws",
              RunConfig(engine=HarborEngine(runner=RecordingRunner(native_log={}))))
    assert captured["timeout"] == 0  # honored, not 1800


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
        ledger_path=tmp_path / "l.ndjson", ctx=ctx_for(tmp_path),
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
        ledger_path=tmp_path / "l.ndjson", ctx=ctx_for(tmp_path),
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
        ledger_path=tmp_path / "l.ndjson", ctx=ctx_for(tmp_path),
        config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0, max_infra_retries=2,
    )
    assert len(find_events(tmp_path / "l.ndjson", "trial")) == 0
    assert len(find_events(tmp_path / "l.ndjson", "trial_infra_failed")) == 3  # 1 + 2 retries


def test_ac5_infra_reason_from_engine_result(tmp_path):
    """RN-14: a REAL engine's infra failure ledgers a reason from the EngineResult,
    not the fake-only task.fake_behavior['infra_reason'] placeholder."""
    from harness.run.engines.harbor import HarborEngine
    from tests.fixtures.run_fakes import FakeDockerRunner

    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p")}  # a real trial: no fake_behavior at all
    order = [Trial(task_id="t", arm="A", repetition=0)]
    ledger = tmp_path / "l.ndjson"
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
        ctx=ctx_for(tmp_path),
        config=RunConfig(engine=HarborEngine(runner=FakeDockerRunner(daemon_error=True))),
        cost_ceiling=100.0, max_infra_retries=0,
    )
    infra = find_events(ledger, "trial_infra_failed")
    assert len(infra) == 1
    assert infra[0]["reason"] == "daemon_error"  # not the "infra_failed" placeholder


def _schedule_one(order, tasks, arms, tmp_path):
    ledger = tmp_path / "l.ndjson"
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
        ctx=ctx_for(tmp_path), config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
    )
    return ledger


def test_ac5_unknown_task_fails_cell_not_run(tmp_path):
    """RN-15: a planned task id absent from the map fails that cell closed
    (trial_infra_failed) and does not abort the run; executed_order still lands."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {}})}
    order = [Trial(task_id="ghost", arm="A", repetition=0),
             Trial(task_id="t", arm="A", repetition=0)]
    ledger = _schedule_one(order, tasks, arms, tmp_path)
    infra = find_events(ledger, "trial_infra_failed")
    assert any(e["task_id"] == "ghost" and e["reason"] == "unknown_task" for e in infra)
    assert len(find_events(ledger, "trial")) == 1  # the good cell still ran
    assert find_events(ledger, "executed_order")  # AC-4 event landed despite the bad cell


def test_ac5_holdout_leak_fails_cell_not_run(tmp_path):
    """RN-15: a canary leaking into the prompt fails that cell closed, never an
    exception that aborts the whole schedule mid-loop."""
    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="LEAKME", holdout_canaries=["LEAKME"],
                       fake_behavior={"native_log": {}})}
    order = [Trial(task_id="t", arm="A", repetition=0)]
    ledger = _schedule_one(order, tasks, arms, tmp_path)
    infra = find_events(ledger, "trial_infra_failed")
    assert len(infra) == 1 and infra[0]["reason"] == "holdout_leak"
    assert find_events(ledger, "executed_order")


def test_ac5_unknown_platform_fails_cell_not_run(tmp_path):
    """RN-15: an arm whose platform has no adapter fails that cell closed."""
    arms = {"A": Arm(name="A", platform="nonesuch", model="x/y-1.0")}
    tasks = {"t": Task(id="t", prompt="p", fake_behavior={"native_log": {}})}
    order = [Trial(task_id="t", arm="A", repetition=0)]
    ledger = _schedule_one(order, tasks, arms, tmp_path)
    infra = find_events(ledger, "trial_infra_failed")
    assert len(infra) == 1 and infra[0]["reason"] == "unknown_platform"
    assert find_events(ledger, "executed_order")


def test_ac5_unexpected_error_fails_cell_closed(tmp_path):
    """RN-15 (review #3): ANY unexpected exception during a trial fails that cell
    closed with a typed reason and does NOT escape schedule() to abort the run."""
    class BoomEngine(FakeEngine):
        def run(self, request):
            raise RuntimeError("kaboom")

    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p"), "u": Task(id="u", prompt="p")}
    order = [Trial(task_id="t", arm="A", repetition=0), Trial(task_id="u", arm="A", repetition=0)]
    ledger = tmp_path / "l.ndjson"
    schedule(order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
             ctx=ctx_for(tmp_path), config=RunConfig(engine=BoomEngine()), cost_ceiling=100.0)
    infra = find_events(ledger, "trial_infra_failed")
    assert len(infra) == 2  # both cells failed closed, run not aborted
    assert all(e["reason"] == "trial_error:RuntimeError" for e in infra)
    assert find_events(ledger, "executed_order")  # AC-4 event still lands


def test_ac5_redaction_error_fails_cell_closed(tmp_path):
    """RN-15 (review #3): a RedactionError maps to a specific reason and fails the
    cell closed instead of escaping schedule()."""
    from harness.run.redact import RedactionError

    class LeakyEngine(FakeEngine):
        def run(self, request):
            raise RedactionError("cannot scrub a root-owned artifact")

    arms = {"A": _arm()}
    tasks = {"t": Task(id="t", prompt="p")}
    order = [Trial(task_id="t", arm="A", repetition=0)]
    ledger = tmp_path / "l.ndjson"
    schedule(order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
             ctx=ctx_for(tmp_path), config=RunConfig(engine=LeakyEngine()), cost_ceiling=100.0)
    infra = find_events(ledger, "trial_infra_failed")
    assert len(infra) == 1 and infra[0]["reason"] == "redaction_error"
