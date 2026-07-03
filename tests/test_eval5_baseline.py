"""EVAL-5 AC-2 — flake baseline quarantine + ledgering; scheduler honors it.

Quarantine is keyed by the task *version* ``(task_id, task_sha)`` [D-2, GR-10]:
the EVAL-5 spec quarantines "that task version", so a clean baseline for a new
version must not launder an old flaky version's quarantine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.grade.baseline import flake_baseline, load_quarantine
from harness.grade.container import GraderUnavailableError, GradingContainer, HoldoutRun
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
    # keyed by the task VERSION, not the bare id [D-2]
    assert load_quarantine(ledger) == {("t1", "sha1")}


def test_ac2_baseline_requires_k_at_least_one(tmp_path):
    """GR-10: k=0 must not ledger a 'clean' verdict from zero evidence."""
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    with pytest.raises(ValueError):
        flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                       workspace=ws, container=GradingContainer(runner=SeqGradeRunner([PASS])), k=0)
    assert find_events(ledger, "flake_baseline") == []


def test_ac2_transient_grader_outage_is_not_flake(tmp_path):
    """GR-8: a transient grader-unavailable outage is NOT flake evidence — it
    fails loud (no verdict, no quarantine) so an infra hiccup can't quarantine a
    healthy task version."""
    class DeadRunner:
        grader_name = "dead"
        def run_holdouts(self, cmd, workspace, holdouts_dir):
            raise GraderUnavailableError("docker daemon down")

    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    with pytest.raises(GraderUnavailableError):
        flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                       workspace=ws, container=GradingContainer(runner=DeadRunner()))
    assert find_events(ledger, "flake_baseline") == []  # no verdict ledgered
    assert load_quarantine(ledger) == set()  # NOT quarantined by the outage


def test_ac2_baseline_runs_are_independent_copies(tmp_path):
    """GR-9: each of the k runs grades a fresh copy of the unmodified workspace,
    so run i's output can't be re-scored as run i+1 (locks the Phase-1 fresh-copy
    guarantee)."""
    seen: list[Path] = []

    class RecordingRunner:
        grader_name = "rec"
        def run_holdouts(self, cmd, workspace, holdouts_dir):
            seen.append(Path(workspace))
            return HoldoutRun(PASS)

    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="s"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=RecordingRunner()), k=3)
    assert len(seen) == 3 and len(set(seen)) == 3  # three distinct fresh copies
    assert ws not in seen  # never the original (evidence untouched)


def test_ac2_quarantined_version_unschedulable(tmp_path):
    """The scheduler refuses a quarantined (task_id, task_sha); a different-sha
    version of the same task id is allowed [D-2]."""
    from harness.run.engines.fake import FakeEngine

    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="bad"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([FAIL] * 5)))
    quarantined = load_quarantine(ledger)  # {("t1", "bad")}

    arms = {"A": Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")}
    order = [Trial(task_id="t1", arm="A", repetition=0)]
    common = dict(
        arms=arms, workspace_root=tmp_path / "run", ctx=fixed_ctx(),
        config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
        quarantined_tasks=quarantined,
    )
    # the quarantined version is refused
    with pytest.raises(QuarantinedTaskError):
        schedule(order, tasks={"t1": Task(id="t1", prompt="p", task_sha="bad")},
                 ledger_path=tmp_path / "run1.ndjson", **common)
    # a DIFFERENT version of the same task id runs fine
    res = schedule(order, tasks={"t1": Task(id="t1", prompt="p", task_sha="good")},
                   ledger_path=tmp_path / "run2.ndjson", **common)
    assert len(res.records) == 1


def test_ac2_quarantine_is_version_scoped(tmp_path):
    """D-2/GR-10: a clean baseline for a NEW task version does not clear the OLD
    flaky version's quarantine — the old version stays quarantined."""
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="old"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([FAIL] * 5)))
    flake_baseline(GradeTask(id="t1", task_sha="new"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([PASS] * 5)))
    # only the old version is quarantined; the new clean version is schedulable
    assert load_quarantine(ledger) == {("t1", "old")}


def test_ac2_same_version_clean_rebaseline_clears(tmp_path):
    """A genuinely fixed flake — the SAME version re-baselined clean — clears that
    version's quarantine (latest-event-wins within a version)."""
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="v1"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([FAIL] * 5)))
    assert load_quarantine(ledger) == {("t1", "v1")}
    flake_baseline(GradeTask(id="t1", task_sha="v1"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([PASS] * 5)))
    assert load_quarantine(ledger) == set()  # same version, now clean
