"""Flake baseline [EVAL-5 §M3, D001, AC-2; F-H2].

Run each task's holdouts ``k=5`` against a defined workspace. Zero tolerance:
any failure quarantines the task *version* and excludes it from run
scheduling. The baseline is ledgered with the task sha. The production caller
is ``bench corpus baseline`` [F-H2], whose workspace contract is the task's
**reference-solution tree** (for fail-to-pass tasks the pre-fix workspace
fails by construction — the flake question is "do the holdouts pass
deterministically when the task is truly solved"); a clean ledgered baseline
is EVAL-8's admission prerequisite, and the quarantine list is honored by
EVAL-4's scheduler.

The zero-tolerance policy is deliberate and cheap (no agent involved) — do not
add a threshold "for pragmatism"; flake-rate policies are deferred until data
argues otherwise. Its disclosed operating characteristic: k zero-tolerance
runs miss a per-run flake of rate p with probability (1-p)**k — ≈90% at
p=0.02, k=5 — so raising ``--k`` (never loosening tolerance) is the lever
when stronger detection is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import find_events
from .deterministic import compute_binary_score, parse_holdout_output
from .fence import GraderUnavailableError, GradingContainerError
from .runners import GradingContainer
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
    workspace_basis: Optional[str] = None,
) -> BaselineOutcome:
    """Run holdouts k times on the given workspace; quarantine on any fail.

    ``workspace_basis`` rides onto the ledgered event [F-H2] so a baseline that
    actually ran against the contracted tree is distinguishable from a
    fabricated event — ``bench corpus baseline`` stamps ``reference_solution``.

    The grader TIER also rides the event, taken from the ``container`` actually
    used (``container.grader_name``) [human-approved 2026-07-07] — ``"docker"``
    is the only TRUSTED tier; a no-daemon runner stamps its own ADVISORY name
    (``"local-exec"``/``"local"``). It is read from the runner, never a caller
    argument, so an ADVISORY baseline cannot be laundered as ``docker``.
    """
    if k < 1:
        raise ValueError(
            f"flake baseline needs k >= 1 (got {k}); a 'clean' verdict cannot come "
            "from zero runs [GR-10]"
        )
    container = container or GradingContainer()
    workspace = Path(workspace)
    # 7B-1/GR-8: probe the grader before the batch. A down daemon must make the
    # baseline inconclusive (GraderUnavailableError propagates, nothing ledgered)
    # rather than quarantining a healthy task version from a docker outage.
    container.preflight()
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
        workspace_basis=workspace_basis,
        # The grader tier comes from the runner actually used, never a caller
        # argument, so an ADVISORY (non-"docker") baseline cannot be recorded
        # as trusted [human-approved 2026-07-07]. Mirrors record_grade's stamp.
        grader=container.grader_name,
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


# --- one-event property registration [EVAL-3 §M7] --------------------------
def _baseline_entrypoint(ctx_dir: str) -> None:
    import json

    from .runners import LocalGradeRunner

    d = Path(ctx_dir)
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "holdout_results.json").write_text(
        json.dumps({"assertions": [{"id": "h1", "result": "pass"}]}), encoding="utf-8"
    )
    flake_baseline(
        GradeTask(id="cand-prop", task_sha="deadbeef"),
        d / "ledger.ndjson",
        EventContext(experiment_id="prop"),
        workspace=ws,
        container=GradingContainer(runner=LocalGradeRunner()),
        workspace_basis="reference_solution",
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("corpus-baseline", _baseline_entrypoint)


_register()
