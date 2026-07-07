"""Groundwork grader plugin [EVAL-5 §M4; verdi-go integration plan §3 Track A1].

Turns groundwork's deterministic architectural verdict on an internal Go task
into an assertion vector, with the **rule id preserved**. Two tiers:

* **REAL tier** (production): shells out to the pinned ``flowmap`` + ``groundwork``
  binaries. The branch graph is regenerated from the workspace copy (never an
  agent-supplied graph — that would forge any verdict, verdi-go doctrine), and
  the policy + base graph are read only from the read-only holdouts side, never
  /workspace [integration plan §2, D2]. The subprocess mechanics live in the
  sibling :mod:`.groundwork_shell` (single responsibility); this module owns only
  the verdict→assertion *mapping*.
* **FIXTURE tier** (no-docker tests): the per-rule verdicts are scripted in
  ``task.fake_plugin_output`` and mapped identically. A production task without
  scripted output takes the real path; if the toolchain or assets are missing it
  fails the grade closed (``cant_grade(plugin_error)``) rather than silently
  contributing zero assertions [F-M-O1].

Verdict semantics [AC-4, consistent with verdi-go epistemics]. A ``BLOCK`` /
``fail`` is a proven gate break → ``fail``; ``STRUCTURALLY-CLEAR`` / ``pass`` →
``pass``; ``NO-STRUCTURAL-SIGNAL`` — the graph has nothing to say (a body-only
change) — is **never** a pass, it is ``abstain``; a caution (the graph cannot
prove a negative — a blind frontier) is ``abstain``; an unknown/future verdict is
``abstain``, never a silent pass [tenet 4].
"""

from __future__ import annotations

from . import groundwork_shell
from .groundwork_shell import (  # re-exported for continuity [F-M-O1]
    GroundworkShellError,
    GroundworkUnavailableError,
)
from ..plugins import GraderPlugin, register_plugin
from ..types import Assertion, AssertionResult, GradeTask

__all__ = [
    "GroundworkGrader",
    "GroundworkUnavailableError",
    "GroundworkShellError",
    "NO_STRUCTURAL_SIGNAL",
]

NO_STRUCTURAL_SIGNAL = "NO-STRUCTURAL-SIGNAL"

# The synthetic id of the top-line, whole-review verdict assertion (distinct from
# any policy rule id, which are groundwork's own — must_not_reach, layering, …).
_VERDICT_ASSERTION_ID = "groundwork:verdict"

# Verdict → assertion result. SOURCE OF TRUTH for the REAL strings: verdi-go
# internal/groundwork/review/artifact.go — the ``Verdict`` constants ``Block`` /
# ``StructurallyClear`` / ``NoStructuralSignal``. The stub originally ASSUMED
# {"pass","fail"}; the real ``review --json`` top-line verdict uses
# BLOCK / STRUCTURALLY-CLEAR, with NO-STRUCTURAL-SIGNAL already correct. The
# fixture-tier aliases ("pass"/"fail") are retained UNCHANGED so existing
# ``fake_plugin_output`` keeps working. Parity with the live binary is pinned by
# ``test_review_verdict_vocabulary_matches_binary`` (real-binary tier) and the
# hermetic mapping tests. An unknown verdict is NOT in this map → abstain.
_VERDICT_MAP = {
    # real groundwork review verdicts (verdi-go review/artifact.go)
    "BLOCK": AssertionResult.failed,
    "STRUCTURALLY-CLEAR": AssertionResult.passed,
    NO_STRUCTURAL_SIGNAL: AssertionResult.abstain,
    # fixture-tier aliases (kept for fake_plugin_output; not emitted by the binary)
    "pass": AssertionResult.passed,
    "fail": AssertionResult.failed,
}


def _finding_detail(finding: dict) -> str:
    """A human detail for one violation/caution: its summary plus the exact edge."""
    summary = str(finding.get("summary") or "").strip()
    frm, to = str(finding.get("from") or ""), str(finding.get("to") or "")
    edge = f"{frm} → {to}" if to else frm
    if summary and edge:
        return f"{summary} [{edge}]"
    return summary or edge or "(no detail)"


@register_plugin
class GroundworkGrader(GraderPlugin):
    id = "groundwork"

    @property
    def _source(self) -> str:
        return f"plugin:{self.id}"

    def grade(self, workspace, task: GradeTask) -> list[Assertion]:
        # FIXTURE tier: scripted per-rule verdicts (kept working unchanged for the
        # no-docker test tier). REAL tier otherwise: shell out to the toolchain.
        if task.fake_plugin_output:
            return self._fake_assertions(task)
        return self._real_assertions(workspace, task)

    def _fake_assertions(self, task: GradeTask) -> list[Assertion]:
        """Map scripted ``{rules: [{id, verdict}]}`` output [FIXTURE tier, F-M-O1]."""
        assertions: list[Assertion] = []
        for rule in task.fake_plugin_output.get("rules", []):
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
                Assertion(id=rule_id, source=self._source, result=result, detail=detail)
            )
        return assertions

    def _real_assertions(self, workspace, task: GradeTask) -> list[Assertion]:
        """Shell out and map the ``review`` artifact [REAL tier, integration §3].

        Any failure — binary/asset missing, workspace won't compile, groundwork
        operational exit 2, malformed JSON — raises out of
        :func:`groundwork_shell.review_artifact`, which grade_trial turns into a
        terminal ``cant_grade(plugin_error)``. A silent empty vector is never a
        grade [F-M-O1, tenet 2]."""
        artifact = groundwork_shell.review_artifact(workspace, task)
        return self._map_review(artifact)

    def _map_review(self, artifact: dict) -> list[Assertion]:
        """Map a parsed ``groundwork review --json`` artifact to assertions.

        Emits one top-line verdict assertion (mapped through :data:`_VERDICT_MAP`;
        unknown → abstain) plus one per-finding assertion preserving groundwork's
        rule id: ``new_violations`` → ``fail`` (a proven gate break), and every
        caution (``new_cautions`` + ``standing_cautions`` — the graph abstaining
        where it cannot prove a negative) → ``abstain``, NEVER ``pass``."""
        assertions: list[Assertion] = []

        verdict = str(artifact.get("verdict", ""))
        if verdict in _VERDICT_MAP:
            result = _VERDICT_MAP[verdict]
            detail = f"groundwork review verdict: {verdict}"
        else:
            # unknown/future verdict → abstain (fail closed), never a silent pass
            result = AssertionResult.abstain
            detail = f"unmapped groundwork verdict {verdict!r}"
        assertions.append(
            Assertion(id=_VERDICT_ASSERTION_ID, source=self._source, result=result, detail=detail)
        )

        for violation in artifact.get("new_violations") or []:
            assertions.append(Assertion(
                id=str(violation.get("rule", "?")), source=self._source,
                result=AssertionResult.failed, detail=_finding_detail(violation),
            ))
        cautions = (artifact.get("new_cautions") or []) + (artifact.get("standing_cautions") or [])
        for caution in cautions:
            assertions.append(Assertion(
                id=str(caution.get("rule", "?")), source=self._source,
                result=AssertionResult.abstain, detail=_finding_detail(caution),
            ))
        return assertions
