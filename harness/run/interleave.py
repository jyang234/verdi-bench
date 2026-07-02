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

from ..adapters.base import Outcome, TrialRecord
from ..ledger import events
from ..ledger.events import EventContext
from ..plan.interleave import Trial
from ..schema.experiment import Arm
from .budget import CostGuard
from .seam import new_trial_id, run_trial
from .types import RunConfig, Task


class QuarantinedTaskError(RuntimeError):
    """A quarantined task version was scheduled [EVAL-5 M3 hook]."""


@dataclass
class ScheduleResult:
    records: list[TrialRecord] = field(default_factory=list)
    executed_order: list[dict] = field(default_factory=list)
    stopped_cost_ceiling: bool = False
    infra_failures: int = 0


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
    guard = CostGuard(ceiling=cost_ceiling)
    out = ScheduleResult()

    for planned in derived_order:
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

        task = tasks[planned.task_id]
        arm = arms[planned.arm]

        record = _run_with_infra_reruns(
            task, arm, planned, workspace_root, ledger_path, ctx, config, max_infra_retries, out
        )
        if record is None:
            continue  # exhausted infra retries; already ledgered as infra_failed

        # completed or timeout ⇒ a real trial event
        events.record_trial(ledger_path, ctx, trial_record=record.model_dump(mode="json"))
        guard.add(record.telemetry.cost)
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

    events.record_executed_order(ledger_path, ctx, order=out.executed_order)
    return out


def _run_with_infra_reruns(
    task, arm, planned, workspace_root, ledger_path, ctx, config, max_infra_retries, out
) -> Optional[TrialRecord]:
    """Run a planned trial, re-running infra failures as brand-new trials."""
    attempts = 0
    while True:
        trial_id = new_trial_id()
        ws = Path(workspace_root) / trial_id
        record = run_trial(
            task, arm, ws, config, repetition=planned.repetition, trial_id=trial_id
        )
        if record.outcome != Outcome.infra_failed:
            return record
        # infra failure: ledger it and re-run as a NEW trial id
        events.record_trial_infra_failed(
            ledger_path,
            ctx,
            trial_id=trial_id,
            task_id=task.id,
            arm=arm.name,
            reason=(task.fake_behavior or {}).get("infra_reason", "infra_failed"),
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
