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
    task_commitment: Optional[dict] = None,
    acknowledged_underpowered: Optional[dict] = None,
    rubric_sha256: Optional[str] = None,
) -> dict:
    """Genesis lock event [AC-2, D004, D008].

    ``task_commitment`` (additive field, EVAL-1-D-6) pins the corpus id/semver
    and a hash over the per-task content shas, so run/grade can refuse tasks
    that were swapped after lock [PL-7]. Optional for compatibility with
    task-less plan flows; required by run/grade when real tasks are present.

    ``rubric_sha256`` (additive field, D-P7-6) commits the judging rubric's
    content hash — the same normalized-text hash the verdict provenance carries
    (``sha256(rubric.read_text("utf-8").encode("utf-8"))``) — so a post-lock
    rubric swap is detectable. Absent on a pre-Phase-7 lock (warn, not refuse).

    ``acknowledged_underpowered`` (additive field, PL-14) carries the
    ``{mde, hypothesized_effect}`` acknowledgment inline **on the lock event**
    when an underpowered design is locked with acknowledgment — one attempted
    operation, one event. It replaces the former separate
    ``acknowledged_underpowered`` event so the one-event-per-operation property
    holds for the documented underpowered path.
    """
    payload = {
        "spec_sha256": spec_sha256,
        "spec_path": spec_path,
        "seed": seed,
        "mde": mde,
        "attestation": {"attested_by": attested_by, "method": method},
    }
    if task_commitment is not None:
        payload["task_commitment"] = task_commitment
    if acknowledged_underpowered is not None:
        payload["acknowledged_underpowered"] = acknowledged_underpowered
    if rubric_sha256 is not None:
        payload["rubric_sha256"] = rubric_sha256
    return emit(ledger_path, ctx, EXPERIMENT_LOCKED, payload)


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
    grader: Optional[str] = None,
    override_of: Optional[str] = None,
) -> dict:
    """``grader`` (additive field) records which grader produced the verdict —
    e.g. ``"docker"`` (a trusted network-less container) vs ``"local"`` (the
    no-daemon path that reads a pre-placed file, ADVISORY). It lets an audit
    distinguish a trusted grade from an advisory/forgeable one.

    ``override_of`` (additive) is the sha256 line hash of a terminal
    ``cant_grade`` this grade re-attempts via ``bench grade --retry-terminal``,
    so a manual override is visible in the event itself [D-P7-2]."""
    payload = {
        "trial_id": trial_id,
        "task_sha": task_sha,
        "assertions": assertions,
        "binary_score": binary_score,
    }
    if fractional_score is not None:
        payload["fractional_score"] = fractional_score
    if grader is not None:
        payload["grader"] = grader
    if override_of is not None:
        payload["override_of"] = override_of
    return emit(ledger_path, ctx, GRADE, payload)


def record_cant_grade(
    ledger_path,
    ctx: EventContext,
    *,
    trial_id: str,
    reason: str,
    override_of: Optional[str] = None,
) -> dict:
    """``override_of`` (additive) is the sha256 line hash of the terminal
    ``cant_grade`` a ``--retry-terminal`` re-attempt overrode; present only on
    an override re-attempt, so every attempt — even a re-failure — is visible
    [D-P7-2]."""
    payload = {"trial_id": trial_id, "reason": reason}
    if override_of is not None:
        payload["override_of"] = override_of
    return emit(ledger_path, ctx, CANT_GRADE, payload)


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
HUMAN_VERDICT = register_event("human_verdict")


def append_verdict(ledger_path, ctx: EventContext, *, verdict: dict) -> dict:
    """Judge verdict — advisory; subsumes CANT_JUDGE via ``winner`` [AC-4]."""
    return emit(ledger_path, ctx, JUDGE_VERDICT, {"verdict": verdict})


def append_human_verdict(
    ledger_path,
    ctx: EventContext,
    *,
    verdict: dict,
    arm_recognized: Optional[bool] = None,
    arm_guess: Optional[str] = None,
    actual_arm: Optional[str] = None,
) -> dict:
    """Human verdict — the only event that closes a comparison [D004, AC-7].

    Shares the Verdict schema family with judge verdicts so kappa is directly
    computable. When captured through the EVAL-7 review flow it additionally
    carries the **blinding-integrity** answers ``{arm_recognized, arm_guess}``
    plus the harness-known ``actual_arm`` (for guess-accuracy scoring); these
    are captured strictly before any reveal [EVAL-7 §4.3, AC-4]. The bare form
    (no integrity) remains valid so the low-level constructor stays reusable.
    """
    payload: dict = {"verdict": verdict}
    if arm_recognized is not None:
        payload["integrity"] = {
            "arm_recognized": arm_recognized,
            "arm_guess": arm_guess,
            "actual_arm": actual_arm,
        }
    return emit(ledger_path, ctx, HUMAN_VERDICT, payload)


# ---------------------------------------------------------------------------
# EVAL-6 events
# ---------------------------------------------------------------------------
FINDINGS_RENDERED = register_event("findings_rendered")
CANT_ANALYZE = register_event("cant_analyze")
SELFCHECK = register_event("selfcheck")


def record_selfcheck(
    ledger_path,
    ctx: EventContext,
    *,
    selected_method: str,
    nominal: float,
    coverage: Optional[float],
    mc_interval: Optional[list],
    n_sim: int,
    n_boot: int,
    n_tasks: int,
    null_model: str,
    passed: bool,
) -> dict:
    """Harness self-validation result [EVAL-1-D008; master plan §7.7].

    Records the coverage self-check: the selected CI method's estimated coverage
    under the recentered null at the realized N, its Monte-Carlo (Wilson 95%)
    interval, and whether the nominal level lies within it (``passed``). A **new
    additive event kind** — old ledgers simply lack it, so no existing hash chain
    is invalidated. The official fence requires a ``passed=true`` selfcheck."""
    return emit(
        ledger_path,
        ctx,
        SELFCHECK,
        {
            "selected_method": selected_method,
            "nominal": nominal,
            "coverage": coverage,
            "mc_interval": mc_interval,
            "n_sim": n_sim,
            "n_boot": n_boot,
            "n_tasks": n_tasks,
            "null_model": null_model,
            "passed": passed,
        },
    )


def record_cant_analyze(
    ledger_path, ctx: EventContext, *, mode: str, reason: str, detail: str = ""
) -> dict:
    """Fail-closed refusal of an analyze render [EVAL-6 §7.2, AN-3].

    A refused official/exploratory render (calibration incomplete, provenance
    invalid, disclosure missing, unregistered metric) lands exactly one event
    instead of escaping the CLI with none. **Additive event type** — old ledgers
    simply lack it, so no existing hash chain is invalidated (EVAL-6 decisions
    ledger + migration note)."""
    return emit(
        ledger_path,
        ctx,
        CANT_ANALYZE,
        {"mode": mode, "reason": reason, "detail": detail},
    )


def record_findings_rendered(
    ledger_path,
    ctx: EventContext,
    *,
    mode: str,
    primary_metric: str,
    ledger_head_hash: str,
    findings_sha256: str,
) -> dict:
    """Provenance of a findings render [EVAL-6 §M6].

    ``analyze`` is a pure function of ``(ledger, seed)``; the CLI writes only the
    findings output and this single event recording what was rendered.
    """
    return emit(
        ledger_path,
        ctx,
        FINDINGS_RENDERED,
        {
            "mode": mode,
            "primary_metric": primary_metric,
            "rendered_head_hash": ledger_head_hash,
            "findings_sha256": findings_sha256,
        },
    )


# ---------------------------------------------------------------------------
# EVAL-7 events
# ---------------------------------------------------------------------------
REVEAL = register_event("reveal")
REVIEW_PACKET_BUILT = register_event("review_packet_built")


def record_review_packet_built(
    ledger_path,
    ctx: EventContext,
    *,
    comparison_id: str,
    task_id: str,
    task_class: str,
    response_map: dict,
    seed: int,
) -> dict:
    """The Response-1/2 ↔ arm map for one blinded review comparison [D-P4-1].

    Emitted by ``bench review build`` when it renders a comparison into the
    packet. ``response_map`` is ``{"1": arm, "2": arm}`` — the authoritative,
    hash-chained record of which arm the human saw as Response 1 vs 2. Reveal,
    ``review record`` (actual_arm / guess accuracy), and EVAL-9 process scoring
    all key off it, so the shape is a versioned contract — additive event type,
    old ledgers simply lack it.
    """
    return emit(
        ledger_path,
        ctx,
        REVIEW_PACKET_BUILT,
        {
            "comparison_id": comparison_id,
            "task_id": task_id,
            "task_class": task_class,
            "response_map": response_map,
            "seed": seed,
        },
    )


def record_reveal(
    ledger_path,
    ctx: EventContext,
    *,
    verdict_event_id: str,
    revealed: dict,
) -> dict:
    """Unblinding checkpoint [EVAL-7 §4.3, AC-4].

    Appendable only after the referenced human verdict exists; discloses the
    judge verdict id and arm identities. This is also the unlock EVAL-9's human
    process scoring keys off [EVAL-9 AC-3] — keep the shape stable.
    """
    return emit(
        ledger_path,
        ctx,
        REVEAL,
        {"verdict_event_id": verdict_event_id, "revealed": revealed},
    )


# ---------------------------------------------------------------------------
# EVAL-8 events
# ---------------------------------------------------------------------------
CURATION_APPROVAL = register_event("curation_approval")
TASK_ADMITTED = register_event("task_admitted")
CALIBRATION_RUN = register_event("calibration_run")
SUBSET_DRAW = register_event("subset_draw")


def record_task_admitted(
    ledger_path,
    ctx: EventContext,
    *,
    candidate_id: str,
    task_sha: str,
    baseline_ref: str,
) -> dict:
    """Ledger a corpus admission decision [EVAL-8 §7.2, CO-4].

    Admission (curation approval AND a clean baseline, both chain-verified) flips
    a candidate to ``admitted``; this event puts that decision on the chain rather
    than only in mutable manifest JSON. **Additive event type** — old ledgers lack
    it, so no existing chain is invalidated."""
    return emit(
        ledger_path,
        ctx,
        TASK_ADMITTED,
        {"candidate_id": candidate_id, "task_sha": task_sha, "baseline_ref": baseline_ref},
    )


def record_calibration_run(
    ledger_path,
    ctx: EventContext,
    *,
    corpus_id: str,
    semver: str,
    kind: str,
    run: dict,
    status: str,
) -> dict:
    """Ledger a corpus calibration run [EVAL-8 §7.2, AC-2, CO-4].

    Calibration status must be chain-anchored, not hand-editable manifest JSON —
    a hand-edited ``full-run-validated`` status otherwise passes the official
    fence. **Additive event type**."""
    return emit(
        ledger_path,
        ctx,
        CALIBRATION_RUN,
        {"corpus_id": corpus_id, "semver": semver, "kind": kind, "run": run, "status": status},
    )


def record_subset_draw(
    ledger_path,
    ctx: EventContext,
    *,
    corpus_id: str,
    semver: str,
    seed: int,
    stratum_key: str,
    task_ids: list,
    strata: dict,
) -> dict:
    """Ledger a seeded stratified calibration-subset draw [EVAL-8 §7.2, CO-9].

    The draw was recorded only in mutable manifest JSON; ledger it so the
    selection is auditable and tamper-evident. **Additive event type**."""
    return emit(
        ledger_path,
        ctx,
        SUBSET_DRAW,
        {
            "corpus_id": corpus_id,
            "semver": semver,
            "seed": seed,
            "stratum_key": stratum_key,
            "task_ids": task_ids,
            "strata": strata,
        },
    )


def record_curation_approval(
    ledger_path,
    ctx: EventContext,
    *,
    candidate_id: str,
    task_sha: str,
    approver: str,
    signature: str,
    signer_public_key: str,
    notes: str = "",
) -> dict:
    """Human curation approval of a mined candidate [EVAL-8 §4.2, AC-4, D-P4-3].

    Admission is this event AND a clean flake baseline — both mechanical
    preconditions; no code path admits a task without this event. ``signature`` /
    ``signer_public_key`` are the approver's Ed25519 attestation over
    ``{candidate_id, task_sha, approver}`` (additive fields): admission verifies
    the signature, that the key is an authorized curator, and that the approver is
    not the miner. Old ledgers simply lack the fields.
    """
    return emit(
        ledger_path,
        ctx,
        CURATION_APPROVAL,
        {
            "candidate_id": candidate_id,
            "task_sha": task_sha,
            "approver": approver,
            "signature": signature,
            "signer_public_key": signer_public_key,
            "notes": notes,
        },
    )


# ---------------------------------------------------------------------------
# EVAL-9 events
# ---------------------------------------------------------------------------
PROCESS_SCORE = register_event("process_score")


def record_process_score(
    ledger_path, ctx: EventContext, *, process_score: dict
) -> dict:
    """Openly-unblinded process score [EVAL-9 §4.2, AC-2].

    Subsumes CANT_SCORE via per-dimension ``CANT_SCORE`` values. The score is
    unrepresentable without unblinded provenance (schema-required)."""
    return emit(ledger_path, ctx, PROCESS_SCORE, {"process_score": process_score})


# ---------------------------------------------------------------------------
# EVAL-10 events
# ---------------------------------------------------------------------------
CONTAMINATION_PROBE = register_event("contamination_probe")


def record_contamination_probe(
    ledger_path, ctx: EventContext, *, probe: dict
) -> dict:
    """One contamination-probe run: per-(arm, task) tri-state outcomes, or a
    fail-closed CANT_PROBE with a reason — never a silent partial probe
    [EVAL-10 §4.4, AC-3]. Canary values are unrepresentable here: the payload
    carries ``sha256(canary)`` only [AC-2]. Additive event type — old ledgers
    simply lack it, no chain invalidated."""
    return emit(ledger_path, ctx, CONTAMINATION_PROBE, {"probe": probe})
