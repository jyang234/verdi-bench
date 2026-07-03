"""EVAL-5 AC-3 / AC-5 — grading, scoring policy, fail-closed."""

from __future__ import annotations

from harness.grade.container import GradingContainer
from harness.grade.deterministic import (
    REASON_CONTAINER,
    REASON_MALFORMED,
    REASON_WORKSPACE_MISSING,
    grade_trial,
)
from harness.grade.types import GradeTask
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.grade_fakes import ScriptedGradeRunner, write_workspace


def _task(**kw):
    return GradeTask(id="t1", task_sha="sha-abc", **kw)


def _grade(tmp_path, output, *, fractional=False, task=None, container_error=False):
    ws = write_workspace(tmp_path)
    container = GradingContainer(
        runner=ScriptedGradeRunner(output, container_error=container_error)
    )
    ledger = tmp_path / "l.ndjson"
    outcome = grade_trial(
        "trial-1", task or _task(), ws, ledger, fixed_ctx(),
        container=container, fractional=fractional,
    )
    return outcome, ledger


def test_ac3_per_assertion_recorded(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"},
        {"id": "h2", "result": "fail", "detail": "expected 3"},
    ]})
    grade = find_events(ledger, "grade")[0]
    assert len(grade["assertions"]) == 2
    ids = {a["id"]: a for a in grade["assertions"]}
    assert ids["h2"]["result"] == "fail"
    assert ids["h2"]["detail"] == "expected 3"


def test_ac3_binary_default(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "pass"},
    ]})
    assert find_events(ledger, "grade")[0]["binary_score"] is True


def test_ac3_binary_fails_on_any_holdout_fail(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "fail"},
    ]})
    assert find_events(ledger, "grade")[0]["binary_score"] is False


def test_ac3_abstain_does_not_count_as_pass(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "abstain"},
    ]})
    # abstain is not pass ⇒ binary is False
    assert find_events(ledger, "grade")[0]["binary_score"] is False


def test_ac3_fractional_requires_prereg(tmp_path):
    # without pre-registration, fractional field is absent
    out, ledger = _grade(tmp_path, {"assertions": [{"id": "h1", "result": "pass"}]},
                         fractional=False)
    grade = find_events(ledger, "grade")[0]
    assert "fractional_score" not in grade


def test_ac3_fractional_present_when_prereg(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "fail"},
    ]}, fractional=True)
    grade = find_events(ledger, "grade")[0]
    assert grade["fractional_score"] == 0.5


def test_ac5_fail_closed_container(tmp_path):
    out, ledger = _grade(tmp_path, None, container_error=True)
    assert out.graded is False
    cg = find_events(ledger, "cant_grade")
    assert len(cg) == 1 and cg[0]["reason"] == REASON_CONTAINER
    assert find_events(ledger, "grade") == []


def test_ac5_fail_closed_malformed(tmp_path):
    out, ledger = _grade(tmp_path, {"garbage": True})  # no 'assertions'
    cg = find_events(ledger, "cant_grade")
    assert len(cg) == 1 and cg[0]["reason"] == REASON_MALFORMED


def test_ac5_fail_closed_workspace_missing(tmp_path):
    container = GradingContainer(runner=ScriptedGradeRunner({"assertions": []}))
    ledger = tmp_path / "l.ndjson"
    out = grade_trial("trial-1", _task(), tmp_path / "nonexistent", ledger, fixed_ctx(),
                      container=container)
    assert out.graded is False
    assert find_events(ledger, "cant_grade")[0]["reason"] == REASON_WORKSPACE_MISSING


def test_ac5_exactly_one_event(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [{"id": "h1", "result": "pass"}]})
    from harness.ledger.query import read_events

    assert len(read_events(ledger)) == 1


def test_ac5_fail_closed_non_iterable_assertions(tmp_path):
    # regression: {"assertions": 5} must fail closed, not crash with TypeError
    out, ledger = _grade(tmp_path, {"assertions": 5})
    assert out.graded is False
    assert find_events(ledger, "cant_grade")[0]["reason"] == REASON_MALFORMED
    from harness.ledger.query import read_events

    assert len(read_events(ledger)) == 1


def test_ac3_empty_holdout_not_vacuous_pass(tmp_path):
    # regression: an empty holdout set must NOT score binary True (would inflate
    # holdout_pass_rate for a trial that verified nothing)
    out, ledger = _grade(tmp_path, {"assertions": []})
    assert find_events(ledger, "grade")[0]["binary_score"] is False


class _FreshCopyRunner:
    """A runner that (like DockerGradeRunner) grades a fresh workspace copy and
    writes its *own* holdout output — records what it was handed."""

    fresh_workspace_copy = True

    def __init__(self, output):
        self.output = output
        self.saw_stale = None
        self.copy_path = None

    def run_holdouts(self, cmd, workspace, holdouts_dir):
        from pathlib import Path

        from harness.grade.container import HoldoutRun

        self.copy_path = Path(workspace)
        self.saw_stale = (Path(workspace) / "holdout_results.json").exists()
        return HoldoutRun(self.output)


def test_grade_ignores_forged_results_and_protects_evidence(tmp_path):
    """GR-1/GR-3: a runner that grades a fresh copy must not see an
    agent-written holdout_results.json (deleted in the copy), must grade a copy
    (not the original), and must not mutate the ledgered trial evidence.
    """
    import json

    ws = write_workspace(tmp_path)
    # the subject agent forges an all-pass results file in its own workspace
    forged = {"assertions": [{"id": "h1", "result": "pass"}]}
    (ws / "holdout_results.json").write_text(json.dumps(forged), encoding="utf-8")

    # the real grader output disagrees (a failure)
    runner = _FreshCopyRunner({"assertions": [{"id": "h1", "result": "fail"}]})
    container = GradingContainer(runner=runner)
    ledger = tmp_path / "l.ndjson"
    grade_trial("trial-1", _task(), ws, ledger, fixed_ctx(), container=container)

    # graded a *copy*, not the original workspace
    assert runner.copy_path != ws
    # the forged file was deleted before grading — the grader saw a clean copy
    assert runner.saw_stale is False
    # the recorded grade reflects the grader's output, not the forged all-pass
    assert find_events(ledger, "grade")[0]["binary_score"] is False
    # the original workspace (ledgered evidence) is untouched
    assert json.loads((ws / "holdout_results.json").read_text()) == forged
