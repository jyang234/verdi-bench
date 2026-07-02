"""Groundwork grader plugin [EVAL-5 §M4].

Runs ``verify``/fitness rules against the workspace for internal Go tasks and
maps each rule verdict to an assertion with the **rule id preserved**. A
``NO-STRUCTURAL-SIGNAL`` verdict maps to ``result=abstain`` — **never** ``pass``
[consistent with verdi-go epistemics, AC-4].

The real implementation shells out to the groundwork tooling; here the rule
verdicts are sourced from ``task.fake_plugin_output`` for deterministic tests,
with the mapping logic identical to production.
"""

from __future__ import annotations

from ..plugins import GraderPlugin, register_plugin
from ..types import Assertion, AssertionResult, GradeTask

NO_STRUCTURAL_SIGNAL = "NO-STRUCTURAL-SIGNAL"

_VERDICT_MAP = {
    "pass": AssertionResult.passed,
    "fail": AssertionResult.failed,
    NO_STRUCTURAL_SIGNAL: AssertionResult.abstain,
}


@register_plugin
class GroundworkGrader(GraderPlugin):
    id = "groundwork"

    def _rule_verdicts(self, workspace, task: GradeTask) -> list[dict]:
        # Production: run `verify`/fitness rules against `workspace`.
        # Test/fixture: read scripted verdicts.
        return (task.fake_plugin_output or {}).get("rules", [])

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
