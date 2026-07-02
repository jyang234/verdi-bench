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
