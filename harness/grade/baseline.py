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
from .container import GradingContainer, GradingContainerError
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
    container = container or GradingContainer()
    workspace = Path(workspace)
    results: list[dict] = []
    clean = True
    for i in range(k):
        try:
            run = container.run(workspace, task.holdouts_dir)
            assertions = parse_holdout_output(run.raw_output)
            passed = compute_binary_score(assertions)
        except (GradingContainerError, ValueError):
            passed = False
        results.append({"run": i, "passed": passed})
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


def load_quarantine(ledger_path) -> set[str]:
    """Task ids whose latest flake baseline quarantined them [scheduler hook].

    Keyed by task_sha so a re-admitted (new sha) version can clear quarantine;
    returns the set of task ids currently quarantined.
    """
    latest_by_sha: dict[str, dict] = {}
    for ev in find_events(ledger_path, events.FLAKE_BASELINE):
        latest_by_sha[ev["task_sha"]] = ev
    return {
        ev["task_id"]
        for ev in latest_by_sha.values()
        if ev["verdict"] == "quarantined"
    }
