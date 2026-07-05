"""Groundwork grader plugin [EVAL-5 §M4].

Runs ``verify``/fitness rules against the workspace for internal Go tasks and
maps each rule verdict to an assertion with the **rule id preserved**. A
``NO-STRUCTURAL-SIGNAL`` verdict maps to ``result=abstain`` — **never** ``pass``
[consistent with verdi-go epistemics, AC-4].

The rule verdicts are sourced from ``task.fake_plugin_output`` — this plugin
is FIXTURE-ONLY until the real groundwork shell-out ships. A production task
that declares the plugin without scripted output fails its grade closed
(``cant_grade(plugin_error)``) rather than silently contributing zero
assertions under a "graded with plugins" appearance [F-M-O1].
"""

from __future__ import annotations

from ..plugins import GraderPlugin, register_plugin
from ..types import Assertion, AssertionResult, GradeTask

NO_STRUCTURAL_SIGNAL = "NO-STRUCTURAL-SIGNAL"


class GroundworkUnavailableError(RuntimeError):
    """The real groundwork tooling is not wired; grading a task that declares
    the plugin must fail closed, never silently no-op [F-M-O1]."""

_VERDICT_MAP = {
    "pass": AssertionResult.passed,
    "fail": AssertionResult.failed,
    NO_STRUCTURAL_SIGNAL: AssertionResult.abstain,
}


@register_plugin
class GroundworkGrader(GraderPlugin):
    id = "groundwork"

    def _rule_verdicts(self, workspace, task: GradeTask) -> list[dict]:
        # F-M-O1: the real groundwork shell-out does not exist yet; a production
        # task reaching this plugin without scripted output previously got an
        # empty assertion list — a silent no-op wearing a plugin's name. Fail
        # loud: grade_trial turns this into a terminal cant_grade(plugin_error).
        if not task.fake_plugin_output:
            raise GroundworkUnavailableError(
                f"groundwork tooling is not wired for task {task.id!r}: the "
                "plugin is fixture-only (fake_plugin_output) until the real "
                "shell-out ships — refusing to contribute zero assertions "
                "silently [F-M-O1]"
            )
        return task.fake_plugin_output.get("rules", [])

    def grade(self, workspace, task: GradeTask) -> list[Assertion]:
        assertions: list[Assertion] = []
        for rule in self._rule_verdicts(workspace, task):
            rule_id = rule["id"]  # rule ids preserved [AC-4]
            verdict = rule["verdict"]
            if verdict not in _VERDICT_MAP:
                # an unknown verdict is a signal we cannot interpret → abstain,
                # never a silent pass
                result = AssertionResult.abstain
                detail = f"unmapped verdict {verdict!r}"
            else:
                result = _VERDICT_MAP[verdict]
                detail = None if verdict != NO_STRUCTURAL_SIGNAL else "no structural signal"
            assertions.append(
                Assertion(id=rule_id, source=f"plugin:{self.id}", result=result, detail=detail)
            )
        return assertions
