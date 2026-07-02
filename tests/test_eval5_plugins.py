"""EVAL-5 AC-4 — plugin seam + groundwork rule mapping."""

from __future__ import annotations

from harness.grade.container import GradingContainer
from harness.grade.deterministic import grade_trial
from harness.grade.plugins import GraderPlugin, get_plugin, register_plugin
from harness.grade.plugins.groundwork import GroundworkGrader
from harness.grade.types import Assertion, AssertionResult, GradeTask
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.grade_fakes import ScriptedGradeRunner, write_workspace


def test_ac4_plugin_contract(tmp_path):
    @register_plugin
    class DummyPlugin(GraderPlugin):
        id = "dummy"

        def grade(self, workspace, task):
            return [Assertion(id="d1", source="plugin:dummy", result=AssertionResult.passed)]

    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="t1", task_sha="s", plugin_ids=["dummy"])
    grade_trial(
        "trial-1", task, ws, ledger, fixed_ctx(),
        container=GradingContainer(runner=ScriptedGradeRunner(
            {"assertions": [{"id": "h1", "result": "pass"}]})),
    )
    grade = find_events(ledger, "grade")[0]
    sources = {a["source"] for a in grade["assertions"]}
    assert "plugin:dummy" in sources and "holdout_test" in sources


def test_ac4_groundwork_plugin_preserves_rule_ids():
    task = GradeTask(id="go1", task_sha="s", fake_plugin_output={"rules": [
        {"id": "RULE-A", "verdict": "pass"},
        {"id": "RULE-B", "verdict": "fail"},
        {"id": "RULE-C", "verdict": "NO-STRUCTURAL-SIGNAL"},
    ]})
    assertions = GroundworkGrader().grade(workspace=None, task=task)
    by_id = {a.id: a for a in assertions}
    assert set(by_id) == {"RULE-A", "RULE-B", "RULE-C"}  # rule ids preserved
    assert by_id["RULE-A"].result == AssertionResult.passed
    assert by_id["RULE-B"].result == AssertionResult.failed
    # NO-STRUCTURAL-SIGNAL ⇒ abstain, NEVER pass
    assert by_id["RULE-C"].result == AssertionResult.abstain


def test_ac4_plugin_abstain_does_not_fail_binary(tmp_path):
    """A plugin abstain must not flip the binary score (holdouts decide it)."""
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="go1", task_sha="s", plugin_ids=["groundwork"],
                     fake_plugin_output={"rules": [{"id": "R", "verdict": "NO-STRUCTURAL-SIGNAL"}]})
    grade_trial(
        "trial-1", task, ws, ledger, fixed_ctx(),
        container=GradingContainer(runner=ScriptedGradeRunner(
            {"assertions": [{"id": "h1", "result": "pass"}]})),
    )
    grade = find_events(ledger, "grade")[0]
    assert grade["binary_score"] is True  # holdout passed; plugin abstain irrelevant


def test_ac4_groundwork_registered():
    assert isinstance(get_plugin("groundwork"), GroundworkGrader)


def test_ac4_unknown_plugin_fails_closed(tmp_path):
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="t1", task_sha="s", plugin_ids=["does-not-exist"])
    out = grade_trial(
        "trial-1", task, ws, ledger, fixed_ctx(),
        container=GradingContainer(runner=ScriptedGradeRunner(
            {"assertions": [{"id": "h1", "result": "pass"}]})),
    )
    assert out.graded is False
    assert find_events(ledger, "cant_grade")[0]["reason"] == "plugin_error"
