"""Flake baseline [EVAL-5 §M3, D001, AC-2].

Run each task's holdouts ``k=5`` against the **unmodified** workspace. Zero
tolerance: any failure quarantines the task *version* and excludes it from run
scheduling. The baseline is ledgered with the task sha. Invoked at
corpus-admission time by EVAL-8 (a clean ledgered baseline is an admission
prerequisite); the quarantine list is honored by EVAL-4's scheduler.

The zero-tolerance policy is deliberate and cheap (no agent involved) — do not
add a threshold "for pragmatism"; flake-rate policies are deferred until data
argues otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import find_events
from .container import GraderUnavailableError, GradingContainer, GradingContainerError
from .deterministic import compute_binary_score, parse_holdout_output
from .types import GradeTask

DEFAULT_K = 5


@dataclass
class BaselineOutcome:
    verdict: str  # "clean" | "quarantined"
    results: list[dict]
    event: dict


def flake_baseline(
    task: GradeTask,
    ledger_path,
    ctx: EventContext,
    *,
    workspace,
    container: Optional[GradingContainer] = None,
    k: int = DEFAULT_K,
) -> BaselineOutcome:
    """Run holdouts k times on the unmodified workspace; quarantine on any fail."""
    if k < 1:
        raise ValueError(
            f"flake baseline needs k >= 1 (got {k}); a 'clean' verdict cannot come "
            "from zero runs [GR-10]"
        )
    container = container or GradingContainer()
    workspace = Path(workspace)
    results: list[dict] = []
    clean = True
    for i in range(k):
        try:
            run = container.run(workspace, task.holdouts_dir)
            assertions = parse_holdout_output(run.raw_output)
            passed = compute_binary_score(assertions)
        except GraderUnavailableError:
            # Transient infra outage — the baseline is inconclusive, NOT flake
            # evidence. Fail loud so admission retries rather than quarantining a
            # healthy task version from a hiccup [GR-8]. Nothing is ledgered.
            raise
        except (GradingContainerError, ValueError) as e:
            # The grader RAN and the holdouts did not cleanly pass (terminal
            # failure or malformed output) — genuine flake evidence [GR-8].
            results.append({"run": i, "passed": False, "detail": str(e)})
            clean = False
            continue
        # Record the assertion vector so a quarantine verdict is auditable from
        # the ledger alone [GR-13], not just {run, passed}.
        results.append({
            "run": i,
            "passed": passed,
            "assertions": [a.model_dump(mode="json") for a in assertions],
        })
        if not passed:
            clean = False

    verdict = "clean" if clean else "quarantined"
    ev = events.record_flake_baseline(
        ledger_path,
        ctx,
        task_id=task.id,
        task_sha=task.task_sha,
        k=k,
        results=results,
        verdict=verdict,
    )
    return BaselineOutcome(verdict=verdict, results=results, event=ev)


def load_quarantine(ledger_path) -> set[tuple[str, str]]:
    """Task *versions* ``(task_id, task_sha)`` whose most recent flake baseline
    quarantined them [D-2, GR-10].

    Keyed by the task version per EVAL-5 AC-2 ("quarantines that task version"):
    a clean baseline for a *new* version does not clear an *old* version's
    quarantine, so a re-mined task can't launder a flaky predecessor. A genuinely
    fixed flake — the *same* version re-baselined clean — does clear, since it is
    latest-event-wins **within a version** (the ledger being append-only). The
    scheduler compares each planned task's ``task_sha`` against this set.
    """
    latest: dict[tuple[str, str], dict] = {}
    for ev in find_events(ledger_path, events.FLAKE_BASELINE):
        latest[(ev["task_id"], ev["task_sha"])] = ev  # append-order ⇒ last wins
    return {key for key, ev in latest.items() if ev["verdict"] == "quarantined"}
