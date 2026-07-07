"""Typed event constructors — the **only** ledger write path [EVAL-3 §4.4].

Every constructor auto-stamps provenance ``{ts, actor, experiment_id,
instrument: {version, git_sha}}`` [AC-6] and appends exactly one line via
:func:`harness.ledger.chain.append_event`. Nothing outside ``harness.ledger``
imports ``chain`` directly — enforced by the import-linter contract — so these
constructors are the sole way an event reaches disk.

Declarative registry [refactor 06 §2]. Each event kind is one row in the
:data:`_EVENT_SPECS` table — an :class:`EventSpec` capturing its exact degrees of
freedom (``required`` / ``omit_if_none`` / ``always_nullable`` fields, plus an
optional ``validate`` shape-check and ``reshape`` payload transform).
:data:`REGISTERED_EVENTS` derives from the table; :func:`emit` refuses any kind
absent from it, so an ad-hoc/mistyped event cannot be written. The 31 public
constructors are thin wrappers that forward their (contractual) arguments to
:func:`build_event`, which assembles the payload from the spec. Assembly order is
irrelevant: ``canonical_line`` sorts keys, so byte-identity depends only on the
(key set, values) — pinned by the constructor-replay golden.

Adding an event kind is three edits: (1) an event-name constant + one
:class:`EventSpec` row in :data:`_EVENT_SPECS`; (2) a thin public constructor
that calls :func:`build_event`; (3) the usual stage-entrypoint registration in
the owning module (the one-event-per-operation property). Unknown event types
are refused.

Size note (the master plan's Phase-5 exit gate asks any >500-line module to
state its reason): the :data:`_EVENT_SPECS` registry (31 event kinds) and the
31 thin constructors forwarding to :func:`build_event` ARE the module — one file
is the sole ledger write path; splitting the table from its wrappers would
fragment that one-write-path property without removing a responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict

from ..version import instrument_identity
from . import chain


class UnregisteredEventError(ValueError):
    """An event type was emitted that no EventSpec row registered."""


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


# ---------------------------------------------------------------------------
# Declarative event registry [refactor 06 §2]
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EventSpec:
    """One event kind's payload policy — the byte-visible degrees of freedom.

    ``required`` fields are always present; ``always_nullable`` are always present
    but may be ``None`` (byte-distinct from omit-if-none); ``omit_if_none`` fields
    appear only when non-``None``. ``validate`` (optional) raises on a malformed
    assembled payload; ``reshape`` (optional) rewrites the field dict before
    assembly (e.g. the ``trial`` sha hoist). Assembly order is irrelevant —
    ``canonical_line`` sorts keys, so only the (key set, values) reach the chain.
    """

    name: str
    required: tuple[str, ...] = ()
    omit_if_none: tuple[str, ...] = ()
    always_nullable: tuple[str, ...] = ()
    validate: Optional[Callable[[dict], None]] = None
    reshape: Optional[Callable[[dict], dict]] = None


def _reshape_trial(fields: dict) -> dict:
    """Hoist the trajectory/flight-recorder/spans shas out of the ``TrialRecord`` dump.

    The shas' single source is the embedded record; hoisting them keeps the
    ledgered ``trial_record`` shape unchanged from pre-EVAL-12 (readers take the
    sha from the EVENT, never the round-tripped record) and each sha lives in
    exactly one place — the top-level event field [EVAL-12-D001, EVAL-24-D001,
    refactor 09 §5 A13].
    """
    record = dict(fields["trial_record"])
    trajectory_sha = record.pop("trajectory_sha", None)
    flight_recorder_sha = record.pop("flight_recorder_sha", None)
    spans_sha = record.pop("spans_sha", None)
    return {
        "trial_record": record,
        "trajectory_sha": trajectory_sha,
        "flight_recorder_sha": flight_recorder_sha,
        "spans_sha": spans_sha,
    }


def _validate_forensics_report(payload: dict) -> None:
    """The exact shape the findings reader indexes — a malformed report refuses
    HERE with a named field, not a KeyError in every later analyze [EVAL-11]."""
    report = payload["forensics_report"]
    if "vocabulary_version" not in report:
        raise ValueError(
            "forensics_report must stamp its vocabulary_version [EVAL-11 AC-1]; "
            "findings from different vocabularies must never merge silently"
        )
    if not isinstance(report.get("flags"), list):
        raise ValueError("forensics_report.flags must be a list (may be empty)")
    coverage = report.get("coverage")
    if not isinstance(coverage, dict) or not {"trials", "covered", "gaps"} <= set(coverage):
        raise ValueError(
            "forensics_report.coverage must carry trials/covered/gaps [AC-6]; "
            f"got {coverage!r}"
        )


def _validate_forensic_spotcheck(payload: dict) -> None:
    stratum = payload["forensic_spotcheck"]["stratum"]
    if stratum not in ("mandatory", "floor"):
        raise ValueError(
            f"stratum must be 'mandatory' or 'floor' (the EVAL-7 review strata), "
            f"got {stratum!r}"
        )


def _validate_forensic_quarantine(payload: dict) -> None:
    if not payload["forensic_quarantine"]["reason"]:
        raise ValueError("a quarantine without a reason is an invisible disposition")


# --- event-name constants (one per registered kind) ------------------------
# EVAL-3
EXPERIMENT_LOCKED = "experiment_locked"
CHAIN_ANCHOR = "chain_anchor"
# EVAL-4
TRIAL = "trial"
TRIAL_INFRA_FAILED = "trial_infra_failed"
RUN_STOPPED_COST_CEILING = "run_stopped_cost_ceiling"
EXECUTED_ORDER = "executed_order"
# EVAL-5
GRADE = "grade"
CANT_GRADE = "cant_grade"
FLAKE_BASELINE = "flake_baseline"
# EVAL-2
JUDGE_VERDICT = "judge_verdict"
HUMAN_VERDICT = "human_verdict"
# EVAL-6
FINDINGS_RENDERED = "findings_rendered"
CANT_ANALYZE = "cant_analyze"
SELFCHECK = "selfcheck"
# EVAL-7
REVEAL = "reveal"
REVIEW_PACKET_BUILT = "review_packet_built"
JUDGE_STOPPED_TOKEN_CEILING = "judge_stopped_token_ceiling"
REVIEW_BATCH = "review_batch"
# EVAL-8
CURATION_APPROVAL = "curation_approval"
TASK_ADMITTED = "task_admitted"
CALIBRATION_RUN = "calibration_run"
SUBSET_DRAW = "subset_draw"
# EVAL-9
PROCESS_SCORE = "process_score"
# EVAL-11
FORENSICS_REPORT = "forensics_report"
FORENSIC_SPOTCHECK = "forensic_spotcheck"
FORENSIC_QUARANTINE = "forensic_quarantine"
# EVAL-10
CONTAMINATION_PROBE = "contamination_probe"
# Control-run reuse [control-reuse plan]
CONTROL_REUSED = "control_reused"
REUSED_TRIAL = "reused_trial"
REUSED_GRADE = "reused_grade"
REUSED_JUDGE_VERDICT = "reused_judge_verdict"


# --- the table: one row per kind, capturing its exact payload policy --------
_EVENT_SPECS: tuple[EventSpec, ...] = (
    # EVAL-3
    EventSpec(
        EXPERIMENT_LOCKED,
        required=("spec_sha256", "spec_path", "seed", "mde", "attestation"),
        omit_if_none=("task_commitment", "acknowledged_underpowered", "rubric_sha256"),
    ),
    EventSpec(CHAIN_ANCHOR, required=("head_hash", "height")),
    # EVAL-4
    EventSpec(
        TRIAL,
        required=("trial_record",),
        omit_if_none=("trajectory_sha", "flight_recorder_sha", "spans_sha"),
        reshape=_reshape_trial,
    ),
    EventSpec(
        TRIAL_INFRA_FAILED,
        required=("trial_id", "task_id", "arm", "reason"),
        omit_if_none=("cost",),
    ),
    EventSpec(RUN_STOPPED_COST_CEILING, required=("accumulated_cost", "ceiling")),
    EventSpec(EXECUTED_ORDER, required=("order",)),
    # EVAL-5
    EventSpec(
        GRADE,
        required=("trial_id", "task_sha", "assertions", "binary_score"),
        omit_if_none=(
            "fractional_score", "grader", "override_of",
            "workspace_sha256", "workspace_walk_version",
        ),
    ),
    EventSpec(CANT_GRADE, required=("trial_id", "reason"), omit_if_none=("override_of",)),
    EventSpec(
        FLAKE_BASELINE,
        required=("task_id", "task_sha", "k", "results", "verdict"),
        omit_if_none=("workspace_basis", "grader"),
    ),
    # EVAL-2
    EventSpec(JUDGE_VERDICT, required=("verdict",)),
    EventSpec(HUMAN_VERDICT, required=("verdict",), omit_if_none=("integrity",)),
    # EVAL-6
    EventSpec(
        SELFCHECK,
        required=("selected_method", "nominal", "n_sim", "n_boot", "n_tasks", "null_model", "passed"),
        always_nullable=("coverage", "mc_interval"),
        omit_if_none=("validation_coverage", "validation_n_sim"),
    ),
    EventSpec(CANT_ANALYZE, required=("mode", "reason", "detail")),
    EventSpec(
        FINDINGS_RENDERED,
        required=("mode", "primary_metric", "rendered_head_hash", "findings_sha256"),
        omit_if_none=("multi_arm_correction",),
    ),
    # EVAL-7
    EventSpec(JUDGE_STOPPED_TOKEN_CEILING, required=("accumulated_tokens", "ceiling")),
    EventSpec(REVIEW_BATCH, required=("batch_id", "comparison_ids", "seed")),
    EventSpec(
        REVIEW_PACKET_BUILT,
        required=("comparison_id", "task_id", "task_class", "response_map", "seed"),
    ),
    EventSpec(REVEAL, required=("verdict_event_id", "revealed")),
    # EVAL-8
    EventSpec(TASK_ADMITTED, required=("candidate_id", "task_sha", "baseline_ref")),
    EventSpec(CALIBRATION_RUN, required=("corpus_id", "semver", "kind", "run", "status")),
    EventSpec(
        SUBSET_DRAW,
        required=("corpus_id", "semver", "seed", "stratum_key", "task_ids", "strata"),
    ),
    EventSpec(
        CURATION_APPROVAL,
        required=("candidate_id", "task_sha", "approver", "signature", "signer_public_key", "notes"),
    ),
    # EVAL-9
    EventSpec(PROCESS_SCORE, required=("process_score",), omit_if_none=("rubric_sha256",)),
    # EVAL-11
    EventSpec(FORENSICS_REPORT, required=("forensics_report",), validate=_validate_forensics_report),
    EventSpec(
        FORENSIC_SPOTCHECK, required=("forensic_spotcheck",), validate=_validate_forensic_spotcheck
    ),
    EventSpec(
        FORENSIC_QUARANTINE, required=("forensic_quarantine",), validate=_validate_forensic_quarantine
    ),
    # EVAL-10
    EventSpec(CONTAMINATION_PROBE, required=("probe",)),
    # Control-run reuse
    EventSpec(
        CONTROL_REUSED,
        required=(
            "source_experiment_id", "source_ledger_head_hash", "bundle_sha256",
            "fingerprint", "control_arm", "cells",
        ),
    ),
    EventSpec(REUSED_TRIAL, required=("trial_record", "reused_from"), omit_if_none=("diff_sha256",)),
    EventSpec(REUSED_GRADE, required=("grade", "reused_from")),
    EventSpec(REUSED_JUDGE_VERDICT, required=("verdict", "reused_from")),
)

_SPEC_BY_NAME: dict[str, EventSpec] = {spec.name: spec for spec in _EVENT_SPECS}

# Registry of known event types, derived from the table. ``emit`` refuses
# anything absent, so an ad-hoc/mistyped event cannot be written.
REGISTERED_EVENTS: set[str] = set(_SPEC_BY_NAME)


def emit(ledger_path: Path | str, ctx: EventContext, event_type: str, payload: dict) -> dict:
    """Assemble → validate provenance → append. The single funnel."""
    if event_type not in REGISTERED_EVENTS:
        raise UnregisteredEventError(
            f"event type {event_type!r} is not registered; add an EventSpec row "
            "before emitting it"
        )
    if "event" in payload or "provenance" in payload or "prev_hash" in payload:
        raise ValueError("payload may not set reserved keys event/provenance/prev_hash")
    envelope = {
        "event": event_type,
        "provenance": build_provenance(ctx),
        **payload,
    }
    return chain.append_event(ledger_path, envelope)


def build_event(
    event_type: str, ledger_path: Path | str, ctx: EventContext, **fields
) -> dict:
    """Assemble a payload from its :class:`EventSpec`, then :func:`emit` it.

    The single generic path the typed constructors funnel through: apply the
    spec's optional ``reshape``; place ``required`` and ``always_nullable`` fields
    unconditionally (the latter present even when ``None``); include
    ``omit_if_none`` fields only when non-``None``; run the spec's optional
    ``validate``; emit. Assembly order is irrelevant — ``canonical_line`` sorts
    keys, so only the (key set, values) reach the chain [refactor 06 §2].
    """
    spec = _SPEC_BY_NAME[event_type]
    if spec.reshape is not None:
        fields = spec.reshape(fields)
    payload: dict = {}
    for key in spec.required:
        payload[key] = fields[key]
    for key in spec.always_nullable:
        payload[key] = fields[key]
    for key in spec.omit_if_none:
        value = fields.get(key)
        if value is not None:
            payload[key] = value
    if spec.validate is not None:
        spec.validate(payload)
    return emit(ledger_path, ctx, event_type, payload)


# ---------------------------------------------------------------------------
# EVAL-3 events
# ---------------------------------------------------------------------------
def record_experiment_locked(
    ledger_path: Path | str,
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
    """Genesis lock event [AC-2, D004, D008]. ``attested_by``/``method`` nest
    under the ``attestation`` block.

    Additive omit-if-None fields: ``task_commitment`` (EVAL-1-D-6) pins the
    corpus id/semver + a hash over the task content shas so run/grade can refuse
    post-lock task swaps [PL-7]; ``rubric_sha256`` (D-P7-6) commits the judging
    rubric's normalized-text hash so a post-lock rubric swap is detectable;
    ``acknowledged_underpowered`` (PL-14) carries the
    ``{mde, hypothesized_effect}`` acknowledgment inline on the lock event, so
    the underpowered path stays one-operation-one-event.
    """
    return build_event(
        EXPERIMENT_LOCKED,
        ledger_path,
        ctx,
        spec_sha256=spec_sha256,
        spec_path=spec_path,
        seed=seed,
        mde=mde,
        attestation={"attested_by": attested_by, "method": method},
        task_commitment=task_commitment,
        acknowledged_underpowered=acknowledged_underpowered,
        rubric_sha256=rubric_sha256,
    )


def record_chain_anchor(
    ledger_path, ctx: EventContext, *, head_hash: str, height: int
) -> dict:
    """External head-hash anchor checkpoint [D008]."""
    return build_event(CHAIN_ANCHOR, ledger_path, ctx, head_hash=head_hash, height=height)


# ---------------------------------------------------------------------------
# EVAL-4 events
# ---------------------------------------------------------------------------
def record_trial(ledger_path, ctx: EventContext, *, trial_record: dict) -> dict:
    """Embeds a normalized TrialRecord [AC-2].

    ``trajectory_sha`` (additive, EVAL-12-D001), ``flight_recorder_sha``
    (EVAL-24-D001), and ``spans_sha`` (refactor 09 §5 A13) bind the persisted
    per-trial trajectory / reasoning / OTLP-span artifacts to the chain. Their
    single source is the ``TrialRecord`` dump: the spec hoists each out of the
    payload (:func:`_reshape_trial`) into a top-level omit-if-None field, so the
    ledgered ``trial_record`` keeps its pre-EVAL-12 shape and each sha lives in
    exactly one place. Readers take the sha from the EVENT
    (``ev.get("trajectory_sha")``), never from a round-tripped ``TrialRecord``
    (transport-only, always ``None`` after re-validation). Absent = an honestly
    absent artifact; no reader may require it and legacy chains are never refused
    over it. ``flight_recorder_sha`` and ``spans_sha`` are operator/forensics-tier
    data, never graded and never in the judge packet.
    """
    return build_event(TRIAL, ledger_path, ctx, trial_record=trial_record)


def record_trial_infra_failed(
    ledger_path, ctx: EventContext, *, trial_id: str, task_id: str, arm: str,
    reason: str, cost: Optional[float] = None
) -> dict:
    """A per-trial infra failure. Additive omit-if-None ``cost`` [PRA-M8] carries
    any spend already incurred before the failure — a post-engine failure
    (redaction/trajectory) happens after the proxy metered the run, so recording
    it keeps the spend enforceable against the ceiling and durable across resume.
    Absent = no spend attributed (the attempt never reached the engine)."""
    return build_event(
        TRIAL_INFRA_FAILED, ledger_path, ctx,
        trial_id=trial_id, task_id=task_id, arm=arm, reason=reason, cost=cost,
    )


def record_run_stopped_cost_ceiling(
    ledger_path, ctx: EventContext, *, accumulated_cost: float, ceiling: float
) -> dict:
    """Cost-ceiling stop [AC-5, AC-7, EVAL-1-D007]."""
    return build_event(
        RUN_STOPPED_COST_CEILING, ledger_path, ctx,
        accumulated_cost=accumulated_cost, ceiling=ceiling,
    )


def record_executed_order(ledger_path, ctx: EventContext, *, order: list) -> dict:
    """The realized interleave [AC-4]."""
    return build_event(EXECUTED_ORDER, ledger_path, ctx, order=order)


# ---------------------------------------------------------------------------
# EVAL-5 events
# ---------------------------------------------------------------------------
def record_grade(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    trial_id: str,
    task_sha: str,
    assertions: list,
    binary_score: bool,
    fractional_score: Optional[float] = None,
    grader: Optional[str] = None,
    override_of: Optional[str] = None,
    workspace_sha256: Optional[str] = None,
    workspace_walk_version: Optional[int] = None,
) -> dict:
    """Deterministic grade. Additive omit-if-None fields:

    ``grader`` — which grader produced it: ``"docker"`` (trusted, network-less)
    vs ``"local"`` (ADVISORY, reads a pre-placed file), so an audit can tell a
    trusted grade from a forgeable one. ``override_of`` — the sha256 line hash of
    a terminal ``cant_grade`` this grade re-attempts via ``--retry-terminal``, so
    the override is visible [D-P7-2]. ``workspace_sha256`` +
    ``workspace_walk_version`` [F-H3] commit the graded solution bytes so the
    forensic/contamination scanners verify evidence instead of trusting live
    disk; pre-existing chains lack them and degrade to a disclosed coverage gap."""
    return build_event(
        GRADE, ledger_path, ctx,
        trial_id=trial_id, task_sha=task_sha, assertions=assertions,
        binary_score=binary_score, fractional_score=fractional_score,
        grader=grader, override_of=override_of, workspace_sha256=workspace_sha256,
        workspace_walk_version=workspace_walk_version,
    )


def record_cant_grade(
    ledger_path: Path | str,
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
    return build_event(
        CANT_GRADE, ledger_path, ctx, trial_id=trial_id, reason=reason, override_of=override_of
    )


def record_flake_baseline(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    task_id: str,
    task_sha: str,
    k: int,
    results: list,
    verdict: str,
    workspace_basis: Optional[str] = None,
    grader: Optional[str] = None,
) -> dict:
    """``workspace_basis`` (additive [F-H2]) records WHAT was baselined —
    ``"reference_solution"`` when produced by ``bench corpus baseline`` against
    the task's reference-solution tree — so a baseline that actually ran is
    distinguishable from a directly-fabricated event.

    ``grader`` (additive, human-approved 2026-07-07) records WHICH grader tier
    produced the k runs — ``"docker"`` is the only TRUSTED tier (network-less
    container, the real grader image); a no-daemon tier is ADVISORY
    (``"local-exec"`` executes the declared holdout on the host, ``"local"``
    reads a pre-placed file). It is stamped from the runner actually used
    (:attr:`GradingContainer.grader_name`), never a caller argument, so an
    ADVISORY run cannot be laundered as ``docker`` on the event — the same
    stamp ``record_grade`` carries. **Absent = unrecorded**: an event that
    predates the field (pre-2026-07-07) does not name its tier, and a reader
    MUST render that as ``unrecorded`` and never default it to the trusted
    ``docker``. Old chains stay valid and chain-verify unchanged; new events
    always carry it. Pre-existing chains simply lack either field."""
    return build_event(
        FLAKE_BASELINE, ledger_path, ctx,
        task_id=task_id, task_sha=task_sha, k=k, results=results,
        verdict=verdict, workspace_basis=workspace_basis, grader=grader,
    )


# ---------------------------------------------------------------------------
# EVAL-2 events
# ---------------------------------------------------------------------------
def append_verdict(ledger_path, ctx: EventContext, *, verdict: dict) -> dict:
    """Judge verdict — advisory; subsumes CANT_JUDGE via ``winner`` [AC-4]."""
    return build_event(JUDGE_VERDICT, ledger_path, ctx, verdict=verdict)


def append_human_verdict(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    verdict: dict,
    arm_recognized: Optional[bool] = None,
    arm_guess: Optional[str] = None,
    actual_arm: Optional[str] = None,
) -> dict:
    """Human verdict — the only event that closes a comparison [D004, AC-7].

    Shares the Verdict schema family with judge verdicts so kappa is directly
    computable. When captured through the EVAL-7 review flow it also carries the
    **blinding-integrity** block ``{arm_recognized, arm_guess, actual_arm}``,
    recorded strictly before any reveal [EVAL-7 §4.3, AC-4]. The block is present
    iff ``arm_recognized`` is supplied (inside it ``arm_guess``/``actual_arm`` are
    always present but nullable); the bare form stays valid for reuse.
    """
    integrity = None
    if arm_recognized is not None:
        integrity = {
            "arm_recognized": arm_recognized,
            "arm_guess": arm_guess,
            "actual_arm": actual_arm,
        }
    return build_event(HUMAN_VERDICT, ledger_path, ctx, verdict=verdict, integrity=integrity)


# ---------------------------------------------------------------------------
# EVAL-6 events
# ---------------------------------------------------------------------------
def record_selfcheck(
    ledger_path: Path | str,
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
    validation_coverage: Optional[float] = None,
    validation_n_sim: Optional[int] = None,
) -> dict:
    """Harness self-validation result [EVAL-1-D008; master plan §7.7].

    The selected CI method's estimated coverage under the recentered null at the
    realized N, its Monte-Carlo (Wilson 95%) ``mc_interval``, and whether the
    nominal level lies within it (``passed``); the official fence requires
    ``passed=true``. ``coverage`` / ``mc_interval`` are always present but
    nullable (byte-distinct from omit-if-none). Additive omit-if-None
    ``validation_coverage`` / ``validation_n_sim`` [F-M-S1] carry the coverage
    re-estimated on an independent sub-seeded stream — the figure the gate
    actually uses; ``coverage`` stays the selection-stream figure."""
    return build_event(
        SELFCHECK, ledger_path, ctx,
        selected_method=selected_method, nominal=nominal, coverage=coverage,
        mc_interval=mc_interval, n_sim=n_sim, n_boot=n_boot, n_tasks=n_tasks,
        null_model=null_model, passed=passed,
        validation_coverage=validation_coverage, validation_n_sim=validation_n_sim,
    )


def record_cant_analyze(
    ledger_path, ctx: EventContext, *, mode: str, reason: str, detail: str = ""
) -> dict:
    """Fail-closed refusal of an analyze render [EVAL-6 §7.2, AN-3].

    A refused render (calibration incomplete, provenance invalid, disclosure
    missing, unregistered metric) lands exactly one event instead of escaping the
    CLI with none. Additive kind — old ledgers simply lack it."""
    return build_event(CANT_ANALYZE, ledger_path, ctx, mode=mode, reason=reason, detail=detail)


def record_findings_rendered(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    mode: str,
    primary_metric: str,
    ledger_head_hash: str,
    findings_sha256: str,
    multi_arm_correction: Optional[str] = None,
) -> dict:
    """Provenance of a findings render [EVAL-6 §M6].

    ``analyze`` is a pure function of ``(ledger, seed)``; the CLI writes only the
    findings output and this single event (the ``ledger_head_hash`` argument
    lands as the ``rendered_head_hash`` field). Additive omit-if-None
    ``multi_arm_correction`` [F-H7] records the applied >2-arm decision policy.
    """
    return build_event(
        FINDINGS_RENDERED, ledger_path, ctx,
        mode=mode, primary_metric=primary_metric,
        rendered_head_hash=ledger_head_hash, findings_sha256=findings_sha256,
        multi_arm_correction=multi_arm_correction,
    )


# ---------------------------------------------------------------------------
# EVAL-7 events
# ---------------------------------------------------------------------------
def record_judge_stopped_token_ceiling(
    ledger_path, ctx: EventContext, *, accumulated_tokens: int, ceiling: int
) -> dict:
    """The judge batch refused further comparisons at the pre-registered
    token ceiling [F-M-J3] — mirroring run_stopped_cost_ceiling: a
    refuse-to-start, never a mid-verdict abort. Additive event kind."""
    return build_event(
        JUDGE_STOPPED_TOKEN_CEILING, ledger_path, ctx,
        accumulated_tokens=accumulated_tokens, ceiling=ceiling,
    )


def record_review_batch(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    batch_id: str,
    comparison_ids: list,
    seed: int,
) -> dict:
    """The reviewed QUEUE as a unit [F-M-O2] — the comparison ids one
    ``bench review build`` invocation put in front of the reviewer. The reveal
    gate refuses any reveal in a batch until EVERY batched comparison has a human
    verdict, closing the loophole where revealing item 1 unblinds items 2..n.
    Additive kind — legacy comparisons belong to no batch (per-item semantics,
    disclosed by absence)."""
    return build_event(
        REVIEW_BATCH, ledger_path, ctx,
        batch_id=batch_id, comparison_ids=list(comparison_ids), seed=seed,
    )


def record_review_packet_built(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    comparison_id: str,
    task_id: str,
    task_class: str,
    response_map: dict,
    seed: int,
) -> dict:
    """The Response-1/2 ↔ arm map for one blinded review comparison [D-P4-1].

    ``response_map`` is ``{"1": arm, "2": arm}`` — the authoritative, hash-chained
    record of which arm the human saw as Response 1 vs 2. Reveal, ``review
    record`` (guess accuracy), and EVAL-9 process scoring all key off it, so the
    shape is a versioned contract. Additive kind — old ledgers lack it.
    """
    return build_event(
        REVIEW_PACKET_BUILT, ledger_path, ctx,
        comparison_id=comparison_id, task_id=task_id, task_class=task_class,
        response_map=response_map, seed=seed,
    )


def record_reveal(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    verdict_event_id: str,
    revealed: dict,
) -> dict:
    """Unblinding checkpoint [EVAL-7 §4.3, AC-4]. Appendable only after the
    referenced human verdict exists; discloses the judge verdict id and arm
    identities. Also the unlock EVAL-9 human process scoring keys off [EVAL-9
    AC-3] — keep the shape stable.
    """
    return build_event(
        REVEAL, ledger_path, ctx, verdict_event_id=verdict_event_id, revealed=revealed
    )


# ---------------------------------------------------------------------------
# EVAL-8 events
# ---------------------------------------------------------------------------
def record_task_admitted(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    candidate_id: str,
    task_sha: str,
    baseline_ref: str,
) -> dict:
    """Ledger a corpus admission decision [EVAL-8 §7.2, CO-4]. Admission (curation
    approval AND a clean baseline, both chain-verified) flips a candidate to
    ``admitted``; this event puts that decision on the chain rather than only in
    mutable manifest JSON. Additive kind — old ledgers lack it."""
    return build_event(
        TASK_ADMITTED, ledger_path, ctx,
        candidate_id=candidate_id, task_sha=task_sha, baseline_ref=baseline_ref,
    )


def record_calibration_run(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    corpus_id: str,
    semver: str,
    kind: str,
    run: dict,
    status: str,
) -> dict:
    """Ledger a corpus calibration run [EVAL-8 §7.2, AC-2, CO-4]. Calibration
    status must be chain-anchored, not hand-editable manifest JSON — a hand-edited
    ``full-run-validated`` status otherwise passes the official fence. Additive."""
    return build_event(
        CALIBRATION_RUN, ledger_path, ctx,
        corpus_id=corpus_id, semver=semver, kind=kind, run=run, status=status,
    )


def record_subset_draw(
    ledger_path: Path | str,
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
    return build_event(
        SUBSET_DRAW, ledger_path, ctx,
        corpus_id=corpus_id, semver=semver, seed=seed, stratum_key=stratum_key,
        task_ids=task_ids, strata=strata,
    )


def record_curation_approval(
    ledger_path: Path | str,
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

    Admission is this event AND a clean flake baseline; no code path admits a task
    without it. ``signature`` / ``signer_public_key`` are the approver's Ed25519
    attestation over ``{candidate_id, task_sha, approver}`` — admission verifies
    the signature, that the key is an authorized curator, and that the approver is
    not the miner. Old ledgers simply lack the fields.
    """
    return build_event(
        CURATION_APPROVAL, ledger_path, ctx,
        candidate_id=candidate_id, task_sha=task_sha, approver=approver,
        signature=signature, signer_public_key=signer_public_key, notes=notes,
    )


# ---------------------------------------------------------------------------
# EVAL-9 events
# ---------------------------------------------------------------------------
def record_process_score(
    ledger_path,
    ctx: EventContext,
    *,
    process_score: dict,
    rubric_sha256: Optional[str] = None,
) -> dict:
    """Openly-unblinded process score [EVAL-9 §4.2, AC-2].

    Subsumes CANT_SCORE via per-dimension ``CANT_SCORE`` values; unrepresentable
    without unblinded provenance (schema-required). Additive omit-if-None
    ``rubric_sha256`` [refactor 06 §7 P4-RUBRIC] commits the content hash of the
    rubric FILE that scored (the lock's normalized-text hash), so a score's rubric
    provenance is recoverable and analyze can attribute custom dimensions later.
    Absent on a pre-P4 score (old bytes unchanged)."""
    return build_event(
        PROCESS_SCORE, ledger_path, ctx, process_score=process_score, rubric_sha256=rubric_sha256
    )


# ---------------------------------------------------------------------------
# EVAL-11 events
# ---------------------------------------------------------------------------
def record_forensics_report(
    ledger_path, ctx: EventContext, *, forensics_report: dict
) -> dict:
    """One forensics scan: metrics, flags, coverage, advisory reviews [EVAL-11].

    Additive kind. The payload stamps its ``vocabulary_version`` [AC-1]; per-trial
    CANT_REVIEW rides inside the ``reviews`` block, so a scan is one event whether
    the advisory pass succeeded, failed closed, or was skipped. The spec's
    ``validate`` refuses a report missing ``vocabulary_version``, a non-list
    ``flags``, or a ``coverage`` block without ``trials``/``covered``/``gaps``."""
    return build_event(FORENSICS_REPORT, ledger_path, ctx, forensics_report=forensics_report)


def record_forensic_spotcheck(
    ledger_path, ctx: EventContext, *, trial_id: str, labels: dict, stratum: str
) -> dict:
    """A human's per-detector spot-check of one trial [EVAL-11 AC-4, D006].

    ``labels`` maps detector ids to booleans; ``stratum`` is the trial's
    EVAL-7 review stratum (``mandatory`` | ``floor``) so IPW kappa applies (the
    spec's ``validate`` refuses any other stratum)."""
    return build_event(
        FORENSIC_SPOTCHECK, ledger_path, ctx,
        forensic_spotcheck={"trial_id": trial_id, "labels": labels, "stratum": stratum},
    )


def record_forensic_quarantine(
    ledger_path, ctx: EventContext, *, trial_id: str, reason: str
) -> dict:
    """A ledgered operator disposition [EVAL-11 D003, D007]: the quarantined
    trial's data leaves the comparisons and every render discloses it. Never
    written by a detector — only the CLI verb a human invokes. The spec's
    ``validate`` refuses a reasonless (invisible) quarantine."""
    return build_event(
        FORENSIC_QUARANTINE, ledger_path, ctx,
        forensic_quarantine={"trial_id": trial_id, "reason": reason},
    )


# ---------------------------------------------------------------------------
# EVAL-10 events
# ---------------------------------------------------------------------------
def record_contamination_probe(
    ledger_path, ctx: EventContext, *, probe: dict
) -> dict:
    """One contamination-probe run: per-(arm, task) tri-state outcomes, or a
    fail-closed CANT_PROBE with a reason — never a silent partial probe
    [EVAL-10 §4.4, AC-3]. Canary values are unrepresentable: the payload carries
    ``sha256(canary)`` only [AC-2]. Additive kind — old ledgers lack it."""
    return build_event(CONTAMINATION_PROBE, ledger_path, ctx, probe=probe)


# ---------------------------------------------------------------------------
# Control-run reuse events [control-reuse plan]
# ---------------------------------------------------------------------------
# Reused control data lands under these DISTINCT event kinds — never the native
# trial / grade / judge_verdict. The official analyze path queries only the
# native kinds, so a reused control is excluded from every official decision by
# construction (structural, not a flag), the same insulation-by-signature the
# rest of the instrument uses. All additive: legacy ledgers simply lack them.
def record_control_reused(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    source_experiment_id: str,
    source_ledger_head_hash: str,
    bundle_sha256: str,
    fingerprint: dict,
    control_arm: str,
    cells: list,
) -> dict:
    """Summary of one control-bundle import [control-reuse plan].

    Records in the importing chain exactly what was pulled and from where: the
    source experiment id + its ledger head hash, the bundle's self sha, the
    matched control fingerprint, the control arm, and the
    ``[{task_id, repetition}]`` cells materialized — the auditable attestation
    that the accompanying ``reused_trial`` / ``reused_grade`` events came from a
    provably-unchanged source, not fabricated. Exactly one per import."""
    return build_event(
        CONTROL_REUSED, ledger_path, ctx,
        source_experiment_id=source_experiment_id,
        source_ledger_head_hash=source_ledger_head_hash,
        bundle_sha256=bundle_sha256, fingerprint=fingerprint,
        control_arm=control_arm, cells=list(cells),
    )


def record_reused_trial(
    ledger_path: Path | str,
    ctx: EventContext,
    *,
    trial_record: dict,
    reused_from: dict,
    diff_sha256: Optional[str] = None,
) -> dict:
    """A control trial imported from a bundle, verbatim, tagged ``reused_from``
    ``{source_experiment_id, bundle_sha256}``. A DISTINCT kind from ``trial`` so
    the official paired path never sees it; only exploratory reuse does. Additive
    omit-if-None ``diff_sha256`` binds the judged-diff snapshot stashed on disk at
    import (the trajectory_sha precedent). Exactly one per imported cell."""
    return build_event(
        REUSED_TRIAL, ledger_path, ctx,
        trial_record=trial_record, reused_from=reused_from, diff_sha256=diff_sha256,
    )


def record_reused_grade(
    ledger_path: Path | str, ctx: EventContext, *, grade: dict, reused_from: dict
) -> dict:
    """A control grade imported from a bundle, verbatim, tagged ``reused_from``.
    Distinct from ``grade`` for the same structural-exclusion reason. Exactly one
    per imported cell."""
    return build_event(REUSED_GRADE, ledger_path, ctx, grade=grade, reused_from=reused_from)


def append_reused_verdict(
    ledger_path: Path | str, ctx: EventContext, *, verdict: dict, reused_from: dict
) -> dict:
    """A fresh judge verdict over a (fresh contender, reused control) pair, tagged
    ``reused_from``. Distinct from ``judge_verdict`` so official judge_preference
    and calibration (which read ``judge_verdict``) never see it — a reused-control
    verdict is exploratory-only. Exactly one per judged comparison."""
    return build_event(
        REUSED_JUDGE_VERDICT, ledger_path, ctx, verdict=verdict, reused_from=reused_from
    )
