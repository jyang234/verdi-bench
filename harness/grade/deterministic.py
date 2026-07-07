"""Deterministic grading [EVAL-5 §M2, AC-3, AC-5].

Every attempted grade appends exactly one event: a deterministic ``grade`` with
the full assertion vector, or one ``cant_grade(reason)``. A grade attempt without
an event is unrepresentable. Note a *transient* cant_grade (a grader that could
not be run, e.g. a docker outage) leaves the trial regradeable [GR-11], so a
later attempt may append another cant_grade and, on recovery, a final grade —
the one-event guarantee is per *attempt*, and a trial's terminal outcome is its
last grade or terminal cant_grade.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..ledger import events
from ..ledger.events import EventContext
from ..run.workspace import WORKSPACE_WALK_VERSION, workspace_sha256
from .fence import (
    GraderUnavailableError,
    GradingContainerError,
    HoldoutResultsMissingError,
)
from .runners import GradingContainer
from .types import Assertion, AssertionResult, GradeTask


class MalformedHoldoutOutput(ValueError):
    pass


# machine-readable cant_grade reasons [AC-5]
REASON_CONTAINER = "container_failure"      # grader ran and failed (terminal)
REASON_DAEMON = "grader_unavailable"        # grader could not be run (transient)
REASON_MALFORMED = "malformed_holdout_output"
REASON_WORKSPACE_MISSING = "workspace_missing"
REASON_PLUGIN = "plugin_error"
REASON_UNKNOWN_TASK = "unknown_task"
REASON_ARTIFACTS_MISSING = "artifacts_missing"
# --runner local with no pre-placed holdout_results.json (terminal): a missing
# grade INPUT on a path with no container, distinct from a grader that ran and
# failed [ux-friction AC-4, F7]. Additive vocabulary in the existing reason
# string field — no event-schema, serialization, or hash-chain change.
REASON_RESULTS_MISSING = "holdout_results_missing"

# Reasons a later grade attempt may resolve (only "the grader could not be run",
# e.g. a docker daemon outage) — these do NOT permanently block regrading
# [GR-11]. Everything else — including a grader that ran and exited nonzero or
# produced no results — is deterministic given the trial + config and stays
# terminal, so a broken grader is not re-attempted on every ``bench grade``.
TRANSIENT_CANT_GRADE = frozenset({REASON_DAEMON})


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
    override_of: Optional[str] = None,
) -> GradeOutcome:
    """Grade one trial. Exactly one event appended (grade or cant_grade).

    ``override_of`` (a terminal ``cant_grade`` line hash) rides onto whichever
    event this attempt produces — grade *or* cant_grade — so a
    ``--retry-terminal`` override is self-describing and a re-failure is still
    visible as a distinct attempt [D-P7-2]."""
    container = container or GradingContainer()
    workspace = Path(workspace)

    def _cant(reason: str) -> GradeOutcome:
        ev = events.record_cant_grade(
            ledger_path, ctx, trial_id=trial_id, reason=reason, override_of=override_of
        )
        return GradeOutcome(event=ev, graded=False)

    if not workspace.exists():
        return _cant(REASON_WORKSPACE_MISSING)

    # 1. Run holdouts in a fresh, network-less container. Distinguish a grader
    # that could not be RUN (transient) from one that ran and FAILED (terminal),
    # so a transient outage is retryable but a broken grader is not re-attempted
    # forever [GR-11]. Both GraderUnavailableError (daemon down) and
    # HoldoutResultsMissingError (--runner local, no pre-placed results INPUT
    # [ux-friction AC-4/F7]) are GradingContainerError subclasses, so they must be
    # caught before the bare container_failure fallback.
    try:
        run = container.run(workspace, task.holdouts_dir)
    except GraderUnavailableError:
        return _cant(REASON_DAEMON)
    except HoldoutResultsMissingError:
        return _cant(REASON_RESULTS_MISSING)
    except GradingContainerError:
        return _cant(REASON_CONTAINER)

    # 2. Parse holdout output (malformed ≠ guessed pass/fail).
    try:
        assertions = parse_holdout_output(run.raw_output)
    except MalformedHoldoutOutput:
        return _cant(REASON_MALFORMED)

    # 3. Invoke declared plugins under the container's isolation discipline
    # [PRA-M6]: the docker path runs them in a fresh-copy, network-less container
    # (no host/network access); the no-daemon local path runs them in-process
    # (ADVISORY, documented). Any error ⇒ cant_grade(plugin_error), fail-closed —
    # a transient container outage is the retryable grader_unavailable.
    try:
        assertions.extend(container.run_plugins(workspace, task.plugin_ids, task))
    except GraderUnavailableError:
        return _cant(REASON_DAEMON)
    except Exception:  # noqa: BLE001 - fail-closed by design
        return _cant(REASON_PLUGIN)

    # 4. Score and record. The grade also commits the workspace's solution
    # bytes to the chain [F-H3] — grading is the moment the workspace becomes
    # evidence, and the scanners verify against this commitment instead of
    # trusting live disk. Hashed over the ORIGINAL workspace (grading ran on a
    # fresh copy, GR-1), with the same canonical walk the verifiers use. A
    # read error here propagates: a crash beats an unverifiable commitment.
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
        grader=container.grader_name,
        override_of=override_of,
        workspace_sha256=workspace_sha256(workspace),
        workspace_walk_version=WORKSPACE_WALK_VERSION,
    )
    return GradeOutcome(event=ev, graded=True)


# --- one-event property registration [EVAL-3 §M7] --------------------------
def _grade_entrypoint(ctx_dir: str) -> None:
    import json

    from ..ledger.events import EventContext
    from .runners import GradingContainer, LocalGradeRunner

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
