"""The run-trial seam [EVAL-4 §M1, AC-1].

``run_trial(task, arm, workspace, config) -> TrialRecord``. The engine is chosen
by config; the seam itself knows nothing of Harbor. It builds the (holdout-free)
request, runs the engine, redacts captured artifacts, normalizes telemetry via
the platform adapter, and assembles the ADVISORY-stamped record. Every deviation
(timeout, infra failure, egress attempt) is recorded as data on the record, never
raised as an exception.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel

from ..adapters import get_adapter
from ..adapters.base import Adapter, Flags, Outcome, Provenance, TrialRecord
from ..adapters.generic import by_model_delta, normalize_generic_by_model
from ..schema.experiment import Arm
from .egress import undeclared_model_egress
from .redact import redact_artifacts
from .settings import MissingProviderKeyError
from .flight_recorder import FlightRecorder, persist_flight_recorder
from .trajectory import TrajectoryCorruptError, TrajectoryRecord, persist_trajectory
from .types import RunConfig, Task, TrialRequest


class HoldoutLeakError(RuntimeError):
    """A holdout canary reached the prompt payload — insulation breach [AC-9]."""


@dataclass
class SpendTracker:
    """The already-incurred spend, threaded through run_trial's post-engine phases
    [refactor 04 §3, PRA-M8].

    The container has run and the proxy has metered it, so any spend is real
    before redaction or capture even begin. This carries the best-available
    figure — the proxy-metered cost until telemetry is normalized, the
    self-reported cost after — so a post-engine failure can ledger exactly the
    spend that was incurred instead of forgetting it. It replaces the
    ``exc.enforcement_cost`` attribute mutation the spend-carry used to smuggle
    through the exception, a cross-module contract invisible to types.
    """

    spend: Optional[float]

    def adopt_telemetry(self, telemetry_cost: Optional[float]) -> None:
        """Prefer the self-reported cost once telemetry is known — matching the
        completed-trial enforcement figure [PRA-M8]. A null self-report leaves the
        proxy figure in place; a null is never imputed [D004]."""
        if telemetry_cost is not None:
            self.spend = telemetry_cost


class PostEngineFailure(Exception):
    """A run_trial phase AFTER the engine ran failed [refactor 04 §3, PRA-M8].

    The container has already run and been metered, so this carries the
    already-incurred ``spend`` explicitly and typed — no longer smuggled on the
    raised exception — for the scheduler to ledger on ``trial_infra_failed`` and
    charge to the cost guard. ``cause`` is the underlying corruption (redaction,
    trajectory, or flight recorder); the scheduler maps it to the closed
    ``trial_infra_failed`` reason vocabulary it owns, so the machine-readable
    reason stays byte-identical to the pre-refactor protocol.
    """

    def __init__(self, *, cause: BaseException, spend: Optional[float]) -> None:
        self.cause = cause
        self.spend = spend
        super().__init__(f"post-engine phase failed ({type(cause).__name__}): {cause}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redacted_native_log(artifacts_dir: Path, trial_id: str) -> dict:
    """The post-redaction agent_log.json — trajectory capture's only input.

    Absent ⇒ ``{}`` (a log-less engine is legitimate; the adapter then reports
    an absent trajectory). Present but unparseable after the scrub ⇒
    :class:`TrajectoryCorruptError` — capture never falls back to the
    pre-redaction in-memory log, that would bypass the scrub [EVAL-12 AC-2].
    """
    path = artifacts_dir / "agent_log.json"
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise TrajectoryCorruptError(
            f"post-redaction native log {path} for {trial_id} is unreadable; "
            "trajectory capture fails closed [EVAL-12 AC-2]: " + str(e)
        ) from e
    if not isinstance(parsed, dict):
        # engines serialize a dict; anything else means the artifact was
        # rewritten out from under the trial — corrupt, not merely absent.
        raise TrajectoryCorruptError(
            f"post-redaction native log {path} for {trial_id} is not an object; "
            "trajectory capture fails closed [EVAL-12 AC-2]"
        )
    return parsed


def new_trial_id(prefix: str = "trial") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class _CaptureContext:
    """The per-trial bindings a capture stage needs [refactor 04 §3]."""

    adapter: Adapter
    trial_id: str
    platform: str
    artifacts_dir: Path
    extra_patterns: list[str]


@dataclass(frozen=True)
class CaptureStage:
    """One post-redaction artifact-capture stage [refactor 04 §3].

    A stage is pure data — how to pull its content out of the redacted native log
    (``normalize``), how to build its versioned record (``build``), where its sha
    lands on the ``TrialRecord`` (``field``), and how to persist it (``persist``).
    A third capture artifact is one more entry in :data:`CAPTURE_STAGES`, not a
    third inline block plus a ``TrialRecord`` edit across four files. ``normalize``
    returning ``None`` is honest absence — no artifact, no sha [EVAL-12 AC-2].
    """

    field: str
    normalize: Callable[[Adapter, dict], Optional[list]]
    build: Callable[["_CaptureContext", list], BaseModel]
    persist: Callable[[BaseModel, Path, list[str]], str]


# The capture stages, in order. Trajectory (the graded actions, EVAL-12) then the
# flight recorder (the operator-tier reasoning, EVAL-24) — a SEPARATE artifact
# from the same redacted native log through the same redaction door.
CAPTURE_STAGES: tuple[CaptureStage, ...] = (
    CaptureStage(
        field="trajectory_sha",
        normalize=lambda adapter, log: adapter.normalize_trajectory(log),
        build=lambda ctx, steps: TrajectoryRecord(
            trial_id=ctx.trial_id, platform=ctx.platform, steps=steps
        ),
        persist=persist_trajectory,
    ),
    CaptureStage(
        field="flight_recorder_sha",
        normalize=lambda adapter, log: adapter.normalize_reasoning(log),
        build=lambda ctx, entries: FlightRecorder(
            trial_id=ctx.trial_id, platform=ctx.platform, entries=entries
        ),
        persist=persist_flight_recorder,
    ),
)


class CapturePipeline:
    """The post-engine capture pipeline [refactor 04 §3].

    One place owns the four cross-cutting concerns the trajectory and
    flight-recorder captures used to duplicate inline:

    * ordering — :meth:`capture` refuses to run before :meth:`redact`, because a
      capture reads the post-redaction on-disk bytes; a capture before the scrub
      would persist a transcript's secrets [AC-8, EVAL-12 AC-2];
    * the infra-failed skip — an already infra-failed trial gets no capture (a
      second failure here would mask the engine's more specific reason);
    * the timeout carve-out — a timeout kill can truncate ``agent_log.json``
      mid-write, so a corrupt post-redaction log on a *timeout* is honest
      absence, not ``trajectory_corrupt``; the RN-17 datapoint (and its spend)
      survives [EVAL-12 AC-2];
    * the PRA-M8 spend-attach — any stage failure raises :class:`PostEngineFailure`
      carrying the :class:`SpendTracker`'s current figure.

    The dual-source invariant is kept by construction: telemetry reads the engine's
    in-memory ``native_log`` (in :func:`run_trial`); captures read the redacted
    on-disk bytes this pipeline scrubbed [refactor 04 §2 contract test].
    """

    def __init__(
        self, tracker: SpendTracker, stages: tuple[CaptureStage, ...] = CAPTURE_STAGES
    ) -> None:
        self._tracker = tracker
        self._stages = stages
        self._redacted = False

    def redact(self, workspace, extra_patterns: list[str]) -> None:
        """Stage 1: scrub the whole workspace before anything persists [AC-8].
        Must run before :meth:`capture`."""
        try:
            redact_artifacts(workspace, extra_patterns)
        except Exception as exc:  # PRA-M8: carry the already-incurred spend, typed
            raise self._failed(exc) from exc
        self._redacted = True

    def capture(self, outcome: Outcome, ctx: _CaptureContext) -> dict[str, Optional[str]]:
        """Stages 2..n: persist each capture artifact from the redacted native log.

        Returns ``{stage.field: sha-or-None}`` — an honest ``None`` for a stage
        whose adapter produced no content, or for an infra-failed / timeout-
        truncated trial with no capturable log.
        """
        if not self._redacted:
            raise RuntimeError(
                "capture() before redact(): captures must read the post-redaction "
                "bytes [refactor 04 §3]"
            )
        shas: dict[str, Optional[str]] = {stage.field: None for stage in self._stages}
        # An already infra-failed trial gets no trial event, so a capture failure
        # here would only mask the engine's more specific reason — skip.
        if outcome == Outcome.infra_failed:
            return shas
        native_log = self._read_redacted_log(outcome, ctx)
        for stage in self._stages:
            shas[stage.field] = self._run_stage(stage, native_log, ctx)
        return shas

    def _read_redacted_log(
        self, outcome: Outcome, ctx: _CaptureContext
    ) -> Optional[dict]:
        try:
            return _redacted_native_log(Path(ctx.artifacts_dir), ctx.trial_id)
        except TrajectoryCorruptError as exc:
            if outcome != Outcome.timeout:
                # PRA-M8: carry the already-incurred spend, typed
                raise self._failed(exc) from exc
            # Timeout carve-out: a truncated log is honest absence, not corrupt —
            # keep the RN-17 timeout datapoint (and its spend) instead of erasing
            # it as trajectory_corrupt.
            return None

    def _run_stage(
        self, stage: CaptureStage, native_log: Optional[dict], ctx: _CaptureContext
    ) -> Optional[str]:
        if native_log is None:
            return None
        content = stage.normalize(ctx.adapter, native_log)
        if content is None:
            return None  # honest absence — no artifact, no sha
        record = stage.build(ctx, content)
        try:
            return stage.persist(record, ctx.artifacts_dir, ctx.extra_patterns)
        except Exception as exc:  # PRA-M8: carry the already-incurred spend, typed
            raise self._failed(exc) from exc

    def _failed(self, cause: BaseException) -> PostEngineFailure:
        return PostEngineFailure(cause=cause, spend=self._tracker.spend)


def run_trial(
    task: Task,
    arm: Arm,
    workspace,
    config: RunConfig,
    *,
    repetition: int = 0,
    trial_id: Optional[str] = None,
    ts: Optional[str] = None,
) -> TrialRecord:
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    trial_id = trial_id or new_trial_id()
    ts = ts or _now_iso()

    # Insulation by construction: the prompt is the task prompt only. Holdouts/
    # canaries are never placed into the request. Defensively verify no canary
    # reaches ANY request-bound channel — prompt, arm payload, or fake behavior —
    # not just the prompt, since all three flow to the engine/workspace.
    prompt = task.prompt
    request_blob = "\n".join(
        [
            prompt,
            json.dumps(arm.payload, sort_keys=True, default=str),
            json.dumps(task.fake_behavior, sort_keys=True, default=str),
            # A3: a task's declared environment is ALSO request-bound (staged into
            # /workspace, injected as env), so a canary must not reach it either.
            json.dumps(task.files, sort_keys=True, default=str),
            json.dumps(task.env, sort_keys=True, default=str),
        ]
    )
    for canary in task.holdout_canaries:
        if canary and canary in request_blob:
            raise HoldoutLeakError(
                f"holdout canary {canary!r} present in request payload for {task.id}"
            )

    # PRA-M2: with a per-arm key allowlist, this arm's container receives ONLY
    # the keys named for it — never the other arm's provider key. Every arm must
    # be listed when the allowlist is in use, so a typo'd/omitted arm fails loud
    # rather than silently running unauthenticated. Without an allowlist, every
    # arm gets every key (pre-M2 default).
    arm_keys = config.provider_keys
    if config.provider_key_names_by_arm is not None:
        if arm.name not in config.provider_key_names_by_arm:
            raise MissingProviderKeyError(
                f"arm {arm.name!r} is not listed in provider_key_names_by_arm; "
                "every arm must declare its provider keys when the per-arm "
                "allowlist is in use [PRA-M2]"
            )
        allowed = set(config.provider_key_names_by_arm[arm.name])
        arm_keys = {k: v for k, v in config.provider_keys.items() if k in allowed}

    request = TrialRequest(
        trial_id=trial_id,
        task_id=task.id,
        prompt=prompt,
        image=task.image,
        arm=arm,
        repetition=repetition,
        workspace=workspace,
        quotas=config.quotas,
        timeout_s=(
            task.timeout_s if task.timeout_s is not None else config.default_timeout_s
        ),
        ts=ts,
        proxy=config.proxy,
        provider_keys=arm_keys,
        fake_behavior=task.fake_behavior,
        files=task.files,  # A3: staged into /workspace by the engine
        env=task.env,  # A3: injected as non-secret env by Harbor
    )

    result = config.engine.run(request)

    # PRA-M8: the container has now run and the proxy has metered it, so any spend
    # is already incurred. If a post-engine step below (redaction, capture) raises,
    # the scheduler ledgers trial_infra_failed — which must carry this spend so it
    # counts against the ceiling and survives resume, instead of burning budget
    # invisibly. The SpendTracker threads the best-available figure explicitly (the
    # telemetry-derived figure replaces the proxy figure once telemetry is
    # normalized below); the CapturePipeline raises the typed PostEngineFailure
    # carrying it — no exception mutation.
    spend = SpendTracker(spend=result.proxy_metered_cost)
    pipeline = CapturePipeline(spend)

    # Stage 1: redact secrets from the whole trial workspace before it persists
    # [AC-8]. RN-7: the agent can write secrets anywhere in the workspace (Harbor
    # mounts it rw and the grader later reads it), not just under artifacts/. RN-9:
    # the injected provider-key VALUES scrub as literals even when their shape is
    # not a known key pattern. (The trial request is mounted read-only OUTSIDE the
    # workspace, so it is not a redaction target [EVAL-4-D-8].)
    extra_patterns = list(config.redact_extra_patterns)
    extra_patterns += [
        re.escape(v) for v in (config.provider_keys or {}).values() if v
    ]
    pipeline.redact(workspace, extra_patterns)

    # Normalize telemetry from agent-native logs [AC-2]; unmeasurable ⇒ null.
    # Dual-source invariant: telemetry reads the engine's in-memory native_log,
    # NOT the on-disk bytes the captures below read [refactor 04 §2].
    adapter = get_adapter(arm.platform)
    telemetry = adapter.normalize(result.native_log)
    # PRA-M8: once telemetry is known, prefer the self-reported cost for any
    # subsequent post-engine failure (matching the completed-trial enforcement).
    spend.adopt_telemetry(telemetry.cost)

    # Per-model attribution [EVAL-21 AC-2, AC-4]: a v2 generic log may split
    # telemetry across the arm's DECLARED models. Self-reported testimony, so
    # it rides flags (the advisory channel) — the authoritative whole-trial
    # telemetry above is untouched, and a sum/total mismatch is surfaced as a
    # delta, never reconciled (the proxy_cost_delta precedent). Parsed ONLY
    # when the adapter speaks the verdi format — a native (claude_code/codex)
    # log that happens to carry a colliding "verdi_log_version" key is
    # agent-controlled content and must never gain verdi semantics or be able
    # to fail the trial — and only for non-infra-failed outcomes, so a corrupt
    # block can never mask the engine's more specific failure reason.
    telemetry_by_model = None
    if adapter.speaks_generic_format and result.outcome != Outcome.infra_failed:
        telemetry_by_model = normalize_generic_by_model(
            result.native_log, arm.declared_models()
        )

    # Stages 2..n: capture the versioned per-trial artifacts from the redacted
    # on-disk native log [EVAL-12 AC-1/AC-2, EVAL-24 AC-1]. The pipeline owns the
    # ordering (strictly after redact — the input is the already-scrubbed
    # agent_log.json, and each persist runs the serialized record through
    # redact_text once more with the same injected-key patterns), the infra-failed
    # skip, the timeout carve-out, and the PRA-M8 spend-attach. An adapter with no
    # content yields None: no artifact, no sha — honest absence, never a fabricated
    # empty record.
    ctx = _CaptureContext(
        adapter=adapter,
        trial_id=trial_id,
        platform=arm.platform,
        artifacts_dir=result.artifacts_dir,
        extra_patterns=extra_patterns,
    )
    shas = pipeline.capture(result.outcome, ctx)
    trajectory_sha = shas["trajectory_sha"]
    flight_recorder_sha = shas["flight_recorder_sha"]

    flags = Flags(egress_violation=result.egress_violation)
    if telemetry_by_model:
        flags.telemetry_by_model = {
            m: t.model_dump(mode="json") for m, t in telemetry_by_model.items()
        }
        delta = by_model_delta(telemetry_by_model, telemetry)
        if delta:
            flags.by_model_delta = delta
    if result.egress_attempts:
        flags.egress_attempts = result.egress_attempts
    # Egress attestation [EVAL-20 AC-6, D003] — policy lives in run/egress.py;
    # the seam only attaches the advisory flag (rides the record, never gates,
    # never fails the trial; the proxy-metered-cost trust pattern).
    if config.proxy is not None:
        undeclared = undeclared_model_egress(
            config.proxy, arm, result.egress_attempts
        )
        if undeclared:
            flags.undeclared_model_egress = undeclared
    if result.proxy_metered_cost is not None:
        # Cross-check signal. Surfaced on the record so the cost guard can enforce
        # on max(self-report, proxy) [RN-2, F-H4] — but it is NEVER written into
        # telemetry.cost: nulls are flagged, not imputed [D004], and the recorded
        # self-report is never reconciled. When both exist, also surface the delta.
        flags.proxy_metered_cost = result.proxy_metered_cost
        if telemetry.cost is not None:
            flags.proxy_cost_delta = round(result.proxy_metered_cost - telemetry.cost, 6)
    if result.failure_reason is not None:
        # carry the engine's infra-failure reason so the scheduler ledgers it
        # instead of the fake-only fake_behavior placeholder [RN-14]
        flags.failure_reason = result.failure_reason

    provenance = Provenance(
        image_digest=result.image_digest,
        agent_binary_version=result.agent_binary_version,
        harbor_version=result.harbor_version,
        engine=result.engine,
        executed_at=result.executed_at or ts,
        quotas=result.quotas or config.quotas,
    )

    return TrialRecord.assemble(
        trial_id=trial_id,
        task_id=task.id,
        arm=arm.name,
        repetition=repetition,
        outcome=result.outcome,
        telemetry=telemetry,
        provenance=provenance,
        exit_status=result.exit_status,
        flags=flags,
        artifacts_path=str(result.artifacts_dir),
        trajectory_sha=trajectory_sha,
        flight_recorder_sha=flight_recorder_sha,
    )
