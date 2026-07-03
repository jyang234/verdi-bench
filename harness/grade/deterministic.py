"""Deterministic grading [EVAL-5 §M2, AC-3, AC-5].

Every trial receives exactly one deterministic grade event containing the full
assertion vector — or exactly one ``cant_grade(reason)``. An attempted grade
without an event is unrepresentable: the two outcomes are the only exits, and
each appends precisely one event.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..ledger import events
from ..ledger.events import EventContext
from .container import GradingContainer, GradingContainerError
from .plugins import get_plugin
from .types import Assertion, AssertionResult, GradeTask


class MalformedHoldoutOutput(ValueError):
    pass


# machine-readable cant_grade reasons [AC-5]
REASON_CONTAINER = "container_failure"
REASON_MALFORMED = "malformed_holdout_output"
REASON_WORKSPACE_MISSING = "workspace_missing"
REASON_PLUGIN = "plugin_error"
REASON_UNKNOWN_TASK = "unknown_task"
REASON_ARTIFACTS_MISSING = "artifacts_missing"

# Reasons a later grade attempt may resolve (e.g. a transient docker daemon
# outage) — these do NOT permanently block regrading [GR-11]. Everything else is
# deterministic given the trial + config and stays terminal.
TRANSIENT_CANT_GRADE = frozenset({REASON_CONTAINER})


@dataclass
class GradeOutcome:
    event: dict
    graded: bool  # True → grade event; False → cant_grade


def parse_holdout_output(raw: dict) -> list[Assertion]:
    """Parse the container's holdout results into holdout-test assertions.

    Format-explicit: a mapping with an ``assertions`` list of
    ``{id, result, detail?}``. Anything else is malformed — never a guessed
    pass/fail [risks §9].
    """
    if not isinstance(raw, dict) or "assertions" not in raw:
        raise MalformedHoldoutOutput("missing 'assertions' list")
    if not isinstance(raw["assertions"], list):
        raise MalformedHoldoutOutput(
            f"'assertions' must be a list, got {type(raw['assertions']).__name__}"
        )
    out: list[Assertion] = []
    for item in raw["assertions"]:
        try:
            out.append(
                Assertion(
                    id=item["id"],
                    source="holdout_test",
                    result=AssertionResult(item["result"]),
                    detail=item.get("detail"),
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            raise MalformedHoldoutOutput(str(e)) from e
    return out


def compute_binary_score(assertions: list[Assertion]) -> bool:
    """All holdout-test assertions pass (abstain does not count as pass) [AC-3].

    Requires at least one holdout assertion: an empty holdout set is **not** a
    vacuous pass (that would silently inflate holdout_pass_rate for a trial that
    verified nothing). Plugin assertions are recorded data and do not affect the
    binary score.
    """
    holdout = [a for a in assertions if a.is_holdout]
    if not holdout:
        return False
    return all(a.result == AssertionResult.passed for a in holdout)


def compute_fractional_score(assertions: list[Assertion]) -> float:
    """Fraction of *scored* (non-abstain) assertions across the full vector that
    passed — abstains carry no signal and are excluded from the denominator.
    Only computed when the lock pre-registered fractional_scoring."""
    scored = [a for a in assertions if a.result != AssertionResult.abstain]
    if not scored:
        return 0.0
    passed = sum(1 for a in scored if a.result == AssertionResult.passed)
    return passed / len(scored)


def grade_trial(
    trial_id: str,
    task: GradeTask,
    workspace,
    ledger_path,
    ctx: EventContext,
    *,
    container: Optional[GradingContainer] = None,
    fractional: bool = False,
) -> GradeOutcome:
    """Grade one trial. Exactly one event appended (grade or cant_grade)."""
    container = container or GradingContainer()
    workspace = Path(workspace)

    def _cant(reason: str) -> GradeOutcome:
        ev = events.record_cant_grade(ledger_path, ctx, trial_id=trial_id, reason=reason)
        return GradeOutcome(event=ev, graded=False)

    if not workspace.exists():
        return _cant(REASON_WORKSPACE_MISSING)

    # 1. Run holdouts in a fresh, network-less container.
    try:
        run = container.run(workspace, task.holdouts_dir)
    except GradingContainerError:
        return _cant(REASON_CONTAINER)

    # 2. Parse holdout output (malformed ≠ guessed pass/fail).
    try:
        assertions = parse_holdout_output(run.raw_output)
    except MalformedHoldoutOutput:
        return _cant(REASON_MALFORMED)

    # 3. Invoke declared plugins; any error ⇒ cant_grade(plugin_error). Fail
    # closed by design (a broken plugin must not crash grading), but keep the
    # reason machine-readable per the event contract.
    try:
        for plugin_id in task.plugin_ids:
            assertions.extend(get_plugin(plugin_id).grade(workspace, task))
    except Exception:  # noqa: BLE001 - fail-closed by design
        return _cant(REASON_PLUGIN)

    # 4. Score and record.
    binary = compute_binary_score(assertions)
    frac = compute_fractional_score(assertions) if fractional else None
    ev = events.record_grade(
        ledger_path,
        ctx,
        trial_id=trial_id,
        task_sha=task.task_sha,
        assertions=[a.model_dump(mode="json") for a in assertions],
        binary_score=binary,
        fractional_score=frac,
    )
    return GradeOutcome(event=ev, graded=True)


# --- one-event property registration [EVAL-3 §M7] --------------------------
def _grade_entrypoint(ctx_dir: str) -> None:
    import json

    from ..ledger.events import EventContext
    from .container import GradingContainer, LocalGradeRunner

    d = Path(ctx_dir)
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "holdout_results.json").write_text(
        json.dumps({"assertions": [{"id": "h1", "result": "pass"}]}), encoding="utf-8"
    )
    grade_trial(
        "trial-x",
        GradeTask(id="t", task_sha="deadbeef"),
        ws,
        d / "ledger.ndjson",
        EventContext(experiment_id="prop"),
        container=GradingContainer(runner=LocalGradeRunner()),
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("grade-trial", _grade_entrypoint)


_register()
