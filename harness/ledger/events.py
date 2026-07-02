"""Typed event constructors — the **only** ledger write path [EVAL-3 §4.4].

Every constructor auto-stamps provenance ``{ts, actor, experiment_id,
instrument: {version, git_sha}}`` [AC-6] and appends exactly one line via
:func:`harness.ledger.chain.append_event`. Nothing outside ``harness.ledger``
imports ``chain`` directly — enforced by the import-linter contract — so these
constructors are the sole way an event reaches disk.

Later stories extend this module with their own event types by calling
:func:`emit` under a registered name; unknown event types are refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict

from ..version import instrument_identity
from . import chain


class UnregisteredEventError(ValueError):
    """An event type was emitted that no constructor registered."""


# Registry of known event types. Constructors register their name; emit refuses
# anything absent, so an ad-hoc/mistyped event cannot be written.
REGISTERED_EVENTS: set[str] = set()


def register_event(name: str) -> str:
    REGISTERED_EVENTS.add(name)
    return name


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventContext:
    """Injected identity/time for a run of ledger writes.

    ``clock`` and ``actor`` are injectable so tests get deterministic
    provenance; production uses wall-clock UTC and the OS user.
    """

    experiment_id: str
    actor: str = "local"
    clock: Callable[[], str] = field(default=_default_clock)


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    git_sha: str


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: str
    actor: str
    experiment_id: str
    instrument: Instrument


def build_provenance(ctx: EventContext) -> dict:
    ident = instrument_identity()
    prov = Provenance(
        ts=ctx.clock(),
        actor=ctx.actor,
        experiment_id=ctx.experiment_id,
        instrument=Instrument(version=ident["version"], git_sha=ident["git_sha"]),
    )
    return prov.model_dump()


def emit(ledger_path, ctx: EventContext, event_type: str, payload: dict) -> dict:
    """Assemble → validate provenance → append. The single funnel."""
    if event_type not in REGISTERED_EVENTS:
        raise UnregisteredEventError(
            f"event type {event_type!r} is not registered; add a constructor via "
            "register_event() before emitting it"
        )
    if "event" in payload or "provenance" in payload or "prev_hash" in payload:
        raise ValueError("payload may not set reserved keys event/provenance/prev_hash")
    envelope = {
        "event": event_type,
        "provenance": build_provenance(ctx),
        **payload,
    }
    return chain.append_event(ledger_path, envelope)


# ---------------------------------------------------------------------------
# EVAL-3 events
# ---------------------------------------------------------------------------
EXPERIMENT_LOCKED = register_event("experiment_locked")
ACKNOWLEDGED_UNDERPOWERED = register_event("acknowledged_underpowered")
CHAIN_ANCHOR = register_event("chain_anchor")


def record_experiment_locked(
    ledger_path,
    ctx: EventContext,
    *,
    spec_sha256: str,
    spec_path: str,
    seed: int,
    mde: dict,
    attested_by: str,
    method: str,
) -> dict:
    """Genesis lock event [AC-2, D004, D008]."""
    return emit(
        ledger_path,
        ctx,
        EXPERIMENT_LOCKED,
        {
            "spec_sha256": spec_sha256,
            "spec_path": spec_path,
            "seed": seed,
            "mde": mde,
            "attestation": {"attested_by": attested_by, "method": method},
        },
    )


def record_acknowledged_underpowered(
    ledger_path, ctx: EventContext, *, mde: float, hypothesized_effect: float
) -> dict:
    """Ledgered acknowledgment that a design is underpowered [D001, AC-4]."""
    return emit(
        ledger_path,
        ctx,
        ACKNOWLEDGED_UNDERPOWERED,
        {"mde": mde, "hypothesized_effect": hypothesized_effect},
    )


def record_chain_anchor(
    ledger_path, ctx: EventContext, *, head_hash: str, height: int
) -> dict:
    """External head-hash anchor checkpoint [D008]."""
    return emit(
        ledger_path,
        ctx,
        CHAIN_ANCHOR,
        {"head_hash": head_hash, "height": height},
    )


# ---------------------------------------------------------------------------
# EVAL-4 events
# ---------------------------------------------------------------------------
TRIAL = register_event("trial")
TRIAL_INFRA_FAILED = register_event("trial_infra_failed")
RUN_STOPPED_COST_CEILING = register_event("run_stopped_cost_ceiling")
EXECUTED_ORDER = register_event("executed_order")


def record_trial(ledger_path, ctx: EventContext, *, trial_record: dict) -> dict:
    """Embeds a normalized TrialRecord [AC-2]."""
    return emit(ledger_path, ctx, TRIAL, {"trial_record": trial_record})


def record_trial_infra_failed(
    ledger_path, ctx: EventContext, *, trial_id: str, task_id: str, arm: str, reason: str
) -> dict:
    return emit(
        ledger_path,
        ctx,
        TRIAL_INFRA_FAILED,
        {"trial_id": trial_id, "task_id": task_id, "arm": arm, "reason": reason},
    )


def record_run_stopped_cost_ceiling(
    ledger_path, ctx: EventContext, *, accumulated_cost: float, ceiling: float
) -> dict:
    """Cost-ceiling stop [AC-5, AC-7, EVAL-1-D007]."""
    return emit(
        ledger_path,
        ctx,
        RUN_STOPPED_COST_CEILING,
        {"accumulated_cost": accumulated_cost, "ceiling": ceiling},
    )


def record_executed_order(ledger_path, ctx: EventContext, *, order: list) -> dict:
    """The realized interleave [AC-4]."""
    return emit(ledger_path, ctx, EXECUTED_ORDER, {"order": order})


# ---------------------------------------------------------------------------
# EVAL-5 events
# ---------------------------------------------------------------------------
GRADE = register_event("grade")
CANT_GRADE = register_event("cant_grade")
FLAKE_BASELINE = register_event("flake_baseline")


def record_grade(
    ledger_path,
    ctx: EventContext,
    *,
    trial_id: str,
    task_sha: str,
    assertions: list,
    binary_score: bool,
    fractional_score: Optional[float] = None,
) -> dict:
    payload = {
        "trial_id": trial_id,
        "task_sha": task_sha,
        "assertions": assertions,
        "binary_score": binary_score,
    }
    if fractional_score is not None:
        payload["fractional_score"] = fractional_score
    return emit(ledger_path, ctx, GRADE, payload)


def record_cant_grade(
    ledger_path, ctx: EventContext, *, trial_id: str, reason: str
) -> dict:
    return emit(ledger_path, ctx, CANT_GRADE, {"trial_id": trial_id, "reason": reason})


def record_flake_baseline(
    ledger_path,
    ctx: EventContext,
    *,
    task_id: str,
    task_sha: str,
    k: int,
    results: list,
    verdict: str,
) -> dict:
    return emit(
        ledger_path,
        ctx,
        FLAKE_BASELINE,
        {
            "task_id": task_id,
            "task_sha": task_sha,
            "k": k,
            "results": results,
            "verdict": verdict,
        },
    )


# ---------------------------------------------------------------------------
# EVAL-2 events
# ---------------------------------------------------------------------------
JUDGE_VERDICT = register_event("judge_verdict")


def append_verdict(ledger_path, ctx: EventContext, *, verdict: dict) -> dict:
    """Judge verdict — advisory; subsumes CANT_JUDGE via ``winner`` [AC-4]."""
    return emit(ledger_path, ctx, JUDGE_VERDICT, {"verdict": verdict})
