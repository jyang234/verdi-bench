"""EVAL-5 AC-2 — flake baseline quarantine + ledgering; scheduler honors it."""

from __future__ import annotations

import pytest

from harness.grade.baseline import flake_baseline, load_quarantine
from harness.grade.container import GradingContainer
from harness.grade.types import GradeTask
from harness.ledger.query import find_events
from harness.plan.interleave import Trial
from harness.run.interleave import QuarantinedTaskError, schedule
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.grade_fakes import SeqGradeRunner, write_workspace

PASS = {"assertions": [{"id": "h1", "result": "pass"}]}
FAIL = {"assertions": [{"id": "h1", "result": "fail"}]}


def test_ac2_baseline_clean(tmp_path):
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    container = GradingContainer(runner=SeqGradeRunner([PASS] * 5))
    out = flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                         workspace=ws, container=container)
    assert out.verdict == "clean"
    ev = find_events(ledger, "flake_baseline")[0]
    assert ev["k"] == 5 and len(ev["results"]) == 5
    assert ev["task_sha"] == "sha1"


def test_ac2_baseline_quarantine(tmp_path):
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    # one of five runs fails ⇒ quarantined (zero tolerance)
    container = GradingContainer(runner=SeqGradeRunner([PASS, PASS, FAIL, PASS, PASS]))
    out = flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                         workspace=ws, container=container)
    assert out.verdict == "quarantined"
    assert load_quarantine(ledger) == {"t1"}


def test_ac2_quarantined_task_unschedulable(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    container = GradingContainer(runner=SeqGradeRunner([FAIL] * 5))
    flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                   workspace=ws, container=container)
    quarantined = load_quarantine(ledger)

    from harness.run.engines.fake import FakeEngine

    arms = {"A": Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")}
    tasks = {"t1": Task(id="t1", prompt="p")}
    order = [Trial(task_id="t1", arm="A", repetition=0)]
    with pytest.raises(QuarantinedTaskError):
        schedule(order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
                 ledger_path=tmp_path / "run.ndjson", ctx=fixed_ctx(),
                 config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
                 quarantined_tasks=quarantined)


def test_ac2_new_clean_baseline_clears_quarantine(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    # a task is quarantined by a flaky baseline...
    flake_baseline(GradeTask(id="t1", task_sha="old"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([FAIL] * 5)))
    assert load_quarantine(ledger) == {"t1"}
    # ...then fixed and re-admitted with a new clean baseline (latest wins)
    flake_baseline(GradeTask(id="t1", task_sha="new"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([PASS] * 5)))
    # the repaired task is schedulable again — quarantine cleared
    assert load_quarantine(ledger) == set()
