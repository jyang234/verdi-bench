"""Interleaved execution [EVAL-4 §M5, AC-4, AC-5, AC-7].

``schedule`` consumes EVAL-3's ``derive_schedule`` output — a flat interleaved
list of ``(task, arm, repetition)``. The API takes **only** the derived order, so
arm-blocked execution is unrepresentable: you cannot ask this function to run all
of arm A then all of arm B.

Lifecycle guarantees:
* No silent retries. An infra failure is ledgered (``trial_infra_failed``) and,
  when re-run, gets a **new** trial id — mutation of an existing trial is
  unrepresentable (ids are write-once, the ledger append-only) [D002, AC-5].
* Timeout is an outcome, not an exception [AC-5].
* Cost ceiling: before each trial start, refuse once past the ceiling and append
  ``run_stopped_cost_ceiling`` [AC-7].
* The realized order is ledgered as ``executed_order`` [AC-4].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..adapters import UnknownPlatformError
from ..adapters.base import Outcome, TrialRecord
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import find_events
from ..plan.interleave import Trial
from ..schema.experiment import Arm
from .budget import CostGuard
from .seam import HoldoutLeakError, new_trial_id, run_trial
from .types import RunConfig, Task


class QuarantinedTaskError(RuntimeError):
    """A quarantined task version was scheduled [EVAL-5 M3 hook]."""


# Per-trial failures that must fail *that cell* closed (a ledgered
# ``trial_infra_failed``) rather than escape ``schedule`` and abort the whole run
# with the executed-order event unwritten [RN-15]. Mapped to a machine-readable
# reason on the event.
_PER_TRIAL_REASONS: dict[type, str] = {
    HoldoutLeakError: "holdout_leak",
    UnknownPlatformError: "unknown_platform",
}


@dataclass
class ScheduleResult:
    records: list[TrialRecord] = field(default_factory=list)
    executed_order: list[dict] = field(default_factory=list)
    stopped_cost_ceiling: bool = False
    infra_failures: int = 0


def _enforcement_cost(
    telemetry_cost: Optional[float], proxy_metered_cost: Optional[float]
) -> Optional[float]:
    """Cost figure the guard enforces on: the self-reported telemetry cost, or —
    when the arm can't self-report (null) — the proxy-metered figure [RN-2].

    Enforcement only: this never fills ``telemetry.cost`` in the record (D004
    keeps nulls null); it exists so a null-cost arm can't spend invisibly.
    """
    return telemetry_cost if telemetry_cost is not None else proxy_metered_cost


def _record_enforcement_cost(record: TrialRecord) -> Optional[float]:
    return _enforcement_cost(
        record.telemetry.cost, getattr(record.flags, "proxy_metered_cost", None)
    )


def _prior_run_state(ledger_path) -> tuple[float, set[tuple]]:
    """Accumulated enforcement spend and completed ``(task, arm, rep)`` cells from
    prior ``trial`` events in this ledger [RN-1].

    A re-run seeds the guard from real prior spend and skips cells that already
    produced a trial, so an interrupted or ceiling-stopped run resumes instead of
    duplicating trials with fresh ids and re-spending from $0. Fresh ledgers have
    no trial events, so this is a no-op on a first run.
    """
    accumulated = 0.0
    done: set[tuple] = set()
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev.get("trial_record", {})
        done.add((rec.get("task_id"), rec.get("arm"), rec.get("repetition")))
        cost = _enforcement_cost(
            (rec.get("telemetry") or {}).get("cost"),
            (rec.get("flags") or {}).get("proxy_metered_cost"),
        )
        if cost is not None:
            accumulated += cost
    return accumulated, done


def _fail_cell(out, ledger_path, ctx, planned, *, reason: str) -> None:
    """Ledger a per-trial infra failure for a cell that could not run and record
    it in the executed order — a bad cell fails closed, never aborts the run and
    never skips the ``executed_order`` event [RN-15]."""
    trial_id = new_trial_id()
    events.record_trial_infra_failed(
        ledger_path, ctx, trial_id=trial_id,
        task_id=planned.task_id, arm=planned.arm, reason=reason,
    )
    out.infra_failures += 1
    out.executed_order.append(
        {
            "trial_id": trial_id,
            "task_id": planned.task_id,
            "arm": planned.arm,
            "repetition": planned.repetition,
            "outcome": Outcome.infra_failed.value,
        }
    )


def schedule(
    derived_order: list[Trial],
    *,
    tasks: dict[str, Task],
    arms: dict[str, Arm],
    workspace_root,
    ledger_path,
    ctx: EventContext,
    config: RunConfig,
    cost_ceiling: float,
    quarantined_tasks: Optional[set[str]] = None,
    max_infra_retries: int = 3,
) -> ScheduleResult:
    workspace_root = Path(workspace_root)
    quarantined = quarantined_tasks or set()
    # Resume from the ledger: seed the guard with prior spend and skip cells that
    # already produced a trial, so a re-run doesn't duplicate or re-spend [RN-1].
    accumulated, done_cells = _prior_run_state(ledger_path)
    guard = CostGuard(ceiling=cost_ceiling, accumulated=accumulated)
    out = ScheduleResult()

    # The executed_order event (AC-4) must land even if a planned trial raises,
    # so the loop is wrapped: per-trial faults fail that cell closed [RN-15] and
    # the order is recorded in a finally.
    try:
        for planned in derived_order:
            cell = (planned.task_id, planned.arm, planned.repetition)
            if cell in done_cells:
                continue  # already executed in a prior run — resume, don't duplicate

            if planned.task_id in quarantined:
                raise QuarantinedTaskError(
                    f"task {planned.task_id} is quarantined (no clean flake baseline) "
                    "and must not be scheduled [EVAL-5]"
                )

            # Cost guard: refuse to start once at/over the ceiling [AC-7].
            if guard.would_exceed():
                events.record_run_stopped_cost_ceiling(
                    ledger_path, ctx, accumulated_cost=guard.accumulated, ceiling=cost_ceiling
                )
                out.stopped_cost_ceiling = True
                break

            # An unknown task/arm id in the schedule fails that cell closed rather
            # than crashing the whole run with a bare KeyError [RN-15].
            if planned.task_id not in tasks:
                _fail_cell(out, ledger_path, ctx, planned, reason="unknown_task")
                continue
            if planned.arm not in arms:
                _fail_cell(out, ledger_path, ctx, planned, reason="unknown_arm")
                continue

            task = tasks[planned.task_id]
            arm = arms[planned.arm]

            try:
                record = _run_with_infra_reruns(
                    task, arm, planned, workspace_root, ledger_path, ctx, config,
                    max_infra_retries, out, guard,
                )
            except tuple(_PER_TRIAL_REASONS) as exc:
                # a canary leak / unknown-platform trial fails closed [RN-15]
                _fail_cell(out, ledger_path, ctx, planned, reason=_PER_TRIAL_REASONS[type(exc)])
                continue
            if out.stopped_cost_ceiling:
                break  # budget exhausted inside the infra-rerun loop [RN-3]
            if record is None:
                continue  # exhausted infra retries; already ledgered as infra_failed

            # completed or timeout ⇒ a real trial event
            events.record_trial(ledger_path, ctx, trial_record=record.model_dump(mode="json"))
            guard.add(_record_enforcement_cost(record))
            out.records.append(record)
            out.executed_order.append(
                {
                    "trial_id": record.trial_id,
                    "task_id": record.task_id,
                    "arm": record.arm,
                    "repetition": record.repetition,
                    "outcome": record.outcome.value,
                }
            )
    finally:
        events.record_executed_order(ledger_path, ctx, order=out.executed_order)
    return out


def _run_with_infra_reruns(
    task, arm, planned, workspace_root, ledger_path, ctx, config, max_infra_retries, out, guard
) -> Optional[TrialRecord]:
    """Run a planned trial, re-running infra failures as brand-new trials.

    The cost guard is checked before each (re)attempt and each failed attempt's
    spend is accumulated, so a storm of costly-but-failing attempts can't burn
    the retry budget past the ceiling [RN-3]. When the guard stops mid-retry it
    records the ceiling stop and signals the caller via ``out.stopped_cost_ceiling``.
    """
    attempts = 0
    while True:
        if guard.would_exceed():
            events.record_run_stopped_cost_ceiling(
                ledger_path, ctx, accumulated_cost=guard.accumulated, ceiling=guard.ceiling
            )
            out.stopped_cost_ceiling = True
            return None
        trial_id = new_trial_id()
        ws = Path(workspace_root) / trial_id
        record = run_trial(
            task, arm, ws, config, repetition=planned.repetition, trial_id=trial_id
        )
        if record.outcome != Outcome.infra_failed:
            return record
        # infra failure: count its spend, ledger it, re-run as a NEW trial id.
        # The reason comes from the engine's result (RN-14), not a fake-only field.
        guard.add(_record_enforcement_cost(record))
        events.record_trial_infra_failed(
            ledger_path,
            ctx,
            trial_id=trial_id,
            task_id=task.id,
            arm=arm.name,
            reason=getattr(record.flags, "failure_reason", None) or "infra_failed",
        )
        out.infra_failures += 1
        out.executed_order.append(
            {
                "trial_id": trial_id,
                "task_id": task.id,
                "arm": arm.name,
                "repetition": planned.repetition,
                "outcome": Outcome.infra_failed.value,
            }
        )
        attempts += 1
        if attempts > max_infra_retries:
            return None


# --- one-event property registration [EVAL-3 §M7] --------------------------
def _run_trial_entrypoint(ctx_dir: str) -> None:
    from .engines.fake import FakeEngine
    from .types import RunConfig

    d = Path(ctx_dir)
    task = Task(id="t", prompt="hello", fake_behavior={"outcome": "completed", "native_log": {}})
    arm = Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")
    rec = run_trial(task, arm, d / "ws", RunConfig(engine=FakeEngine()))
    events.record_trial(
        d / "ledger.ndjson",
        EventContext(experiment_id="prop"),
        trial_record=rec.model_dump(mode="json"),
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("run-trial", _run_trial_entrypoint)


_register()
