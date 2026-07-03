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
from .redact import RedactionError
from .seam import HoldoutLeakError, new_trial_id, run_trial
from .types import RunConfig, Task


class QuarantinedTaskError(RuntimeError):
    """A quarantined task version was scheduled [EVAL-5 M3 hook]."""


# Known per-trial failures get a specific machine-readable reason; any OTHER
# exception from running a trial still fails *that cell* closed (a ledgered
# ``trial_infra_failed``) with a generic reason, so no per-trial fault escapes
# ``schedule`` and aborts the whole run [RN-15]. Matched by ``isinstance`` so
# subclasses map correctly.
_PER_TRIAL_REASONS: dict[type, str] = {
    HoldoutLeakError: "holdout_leak",
    UnknownPlatformError: "unknown_platform",
    RedactionError: "redaction_error",
}


def _reason_for(exc: BaseException) -> str:
    """Machine-readable trial_infra_failed reason for a per-trial exception —
    ``isinstance`` so a subclass maps to its base, with a typed fallback so an
    unforeseen failure is still surfaced (never swallowed, never escapes)."""
    for exc_type, reason in _PER_TRIAL_REASONS.items():
        if isinstance(exc, exc_type):
            return reason
    return f"trial_error:{type(exc).__name__}"


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


def _prior_run_state(ledger_path) -> tuple[float, set[tuple], list[dict]]:
    """Prior spend, completed ``(task, arm, rep)`` cells, and the last realized
    order from this ledger, so a re-run resumes instead of duplicating [RN-1].

    A re-run seeds the guard from real prior spend and skips cells that already
    produced a trial. Fresh ledgers have no such events, so this is a no-op on a
    first run.

    Spend is summed from completed ``trial`` events; additionally, a prior
    ``run_stopped_cost_ceiling`` snapshots the FULL guard spend at the stop
    (completed trials AND infra-failed attempts, RN-3), so its ``accumulated_cost``
    is taken as a lower bound — otherwise infra-attempt spend, which
    ``trial_infra_failed`` does not carry, would be forgotten and a resumed run
    could re-spend past the pre-registered ceiling. (A crash *before* a ceiling
    stop still loses in-flight infra spend; making that durable needs a cost field
    on the infra event — see the review note.)
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
    for ev in find_events(ledger_path, events.RUN_STOPPED_COST_CEILING):
        accumulated = max(accumulated, ev.get("accumulated_cost", 0.0) or 0.0)
    # Seed the realized order with the last one so the resume's executed_order
    # event is the COMPLETE order, not a fragment that hides a confound [AC-4].
    order_evs = find_events(ledger_path, events.EXECUTED_ORDER)
    prior_order = list(order_evs[-1].get("order", [])) if order_evs else []
    return accumulated, done, prior_order


def _record_ceiling_stop(out: "ScheduleResult", ledger_path, ctx, guard: CostGuard) -> None:
    """Record the cost-ceiling stop exactly once and flag it [AC-7]. Shared by the
    main loop and the infra-rerun loop so the stop is recorded one way."""
    events.record_run_stopped_cost_ceiling(
        ledger_path, ctx, accumulated_cost=guard.accumulated, ceiling=guard.ceiling
    )
    out.stopped_cost_ceiling = True


def _assert_not_quarantined(derived_order, tasks, quarantined: set) -> None:
    """Pre-flight [D-2]: refuse a run whose plan schedules a quarantined task
    *version*, before any trial executes — a policy halt, not a mid-loop abort
    after partial execution. Fails loud if a scheduled task lacks the ``task_sha``
    needed to check quarantine (a missing version id must not silently disable the
    safety gate)."""
    if not quarantined:
        return
    for planned in derived_order:
        task = tasks.get(planned.task_id)
        if task is None:
            continue  # unknown task id: handled per-cell in the loop
        if task.task_sha is None:
            raise QuarantinedTaskError(
                f"cannot enforce quarantine for task {planned.task_id!r}: it carries "
                "no task_sha (version identity). Refusing [EVAL-5, fail-loudly]."
            )
        if (planned.task_id, task.task_sha) in quarantined:
            raise QuarantinedTaskError(
                f"task version ({planned.task_id}, {task.task_sha}) is quarantined "
                "(no clean flake baseline) and must not be scheduled [EVAL-5]"
            )


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
    quarantined_tasks: Optional[set[tuple[str, str]]] = None,
    max_infra_retries: int = 3,
) -> ScheduleResult:
    workspace_root = Path(workspace_root)
    quarantined = quarantined_tasks or set()

    # Pre-flight policy gate: refuse a plan that schedules a quarantined task
    # version before any trial runs (loud halt, no partial execution) [D-2].
    _assert_not_quarantined(derived_order, tasks, quarantined)

    # Resume from the ledger: seed the guard with prior spend, skip cells that
    # already produced a trial, and seed the realized order so the resume's
    # executed_order event is complete, not a fragment [RN-1, AC-4].
    accumulated, done_cells, prior_order = _prior_run_state(ledger_path)
    guard = CostGuard(ceiling=cost_ceiling, accumulated=accumulated)
    out = ScheduleResult()
    out.executed_order = list(prior_order)

    # The executed_order event (AC-4) must land even if a planned trial raises,
    # so the loop is wrapped: per-trial faults fail that cell closed [RN-15] and
    # the order is recorded in a finally.
    try:
        for planned in derived_order:
            cell = (planned.task_id, planned.arm, planned.repetition)
            if cell in done_cells:
                continue  # already executed in a prior run — resume, don't duplicate

            # Cost guard: refuse to start once at/over the ceiling [AC-7].
            if guard.would_exceed():
                _record_ceiling_stop(out, ledger_path, ctx, guard)
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
            except Exception as exc:  # noqa: BLE001 — ANY per-trial fault fails THIS
                # cell closed (ledgered, reason-tagged), never escapes to abort the
                # whole run [RN-15]. Not swallowed: surfaced as trial_infra_failed.
                _fail_cell(out, ledger_path, ctx, planned, reason=_reason_for(exc))
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
            _record_ceiling_stop(out, ledger_path, ctx, guard)
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
