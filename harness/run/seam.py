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
from typing import Optional

from ..adapters import get_adapter
from ..adapters.base import Flags, Outcome, Provenance, TrialRecord
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
    # is already incurred. If a post-engine step below (redaction, trajectory)
    # raises, the scheduler ledgers trial_infra_failed — which must carry this
    # spend so it counts against the ceiling and survives resume, instead of
    # burning budget invisibly. The SpendTracker threads the best-available figure
    # explicitly (the telemetry-derived figure replaces the proxy figure once
    # telemetry is normalized below), and a post-engine failure raises the typed
    # PostEngineFailure carrying it — no exception mutation.
    spend = SpendTracker(spend=result.proxy_metered_cost)

    # Redact secrets from the whole trial workspace before it persists [AC-8].
    # RN-7: the agent can write secrets anywhere in the workspace (Harbor mounts
    # it rw and the grader later reads it), not just under artifacts/. RN-9: the
    # injected provider-key VALUES scrub as literals even when their shape is not
    # a known key pattern. (The trial request is mounted read-only OUTSIDE the
    # workspace, so it is not a redaction target [EVAL-4-D-8].)
    extra_patterns = list(config.redact_extra_patterns)
    extra_patterns += [
        re.escape(v) for v in (config.provider_keys or {}).values() if v
    ]
    try:
        redact_artifacts(workspace, extra_patterns)
    except Exception as exc:  # PRA-M8: carry the already-incurred spend, typed
        raise PostEngineFailure(cause=exc, spend=spend.spend) from exc

    # Normalize telemetry from agent-native logs [AC-2]; unmeasurable ⇒ null.
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

    # Trajectory capture [EVAL-12 AC-1, AC-2] — strictly after redact_artifacts:
    # the input is the already-scrubbed on-disk agent_log.json (a trajectory is
    # a transcript, and transcripts leak secrets), and persist_trajectory runs
    # the serialized record through redact_text once more with the same
    # injected-key patterns. An adapter with no trajectory content yields None:
    # no artifact, no sha — honest absence, never a fabricated empty record.
    # A corrupt/unwritable trajectory raises TrajectoryCorruptError, which the
    # scheduler ledgers as trial_infra_failed(trajectory_corrupt). An already
    # infra-failed trial (e.g. RN-17 telemetry_corrupt) is not captured: it gets
    # no trial event, and a second failure here would mask the engine's more
    # specific reason.
    trajectory_sha: Optional[str] = None
    flight_recorder_sha: Optional[str] = None
    if result.outcome != Outcome.infra_failed:
        try:
            native_log = _redacted_native_log(Path(result.artifacts_dir), trial_id)
        except TrajectoryCorruptError as exc:
            if result.outcome != Outcome.timeout:
                # PRA-M8: carry the already-incurred spend, typed
                raise PostEngineFailure(cause=exc, spend=spend.spend) from exc
            # A timeout kill can truncate agent_log.json mid-write. The timeout
            # outcome is data (the RN-17 seam keeps it); destroying the trial as
            # trajectory_corrupt would erase the datapoint and its spend. The
            # trajectory is honestly absent instead — a COMPLETED trial with a
            # corrupt post-redaction log still fails closed above.
            native_log = None
        steps = (
            adapter.normalize_trajectory(native_log) if native_log is not None else None
        )
        if steps is not None:
            try:
                trajectory_sha = persist_trajectory(
                    TrajectoryRecord(trial_id=trial_id, platform=arm.platform, steps=steps),
                    result.artifacts_dir,
                    extra_patterns,
                )
            except Exception as exc:  # PRA-M8: carry the already-incurred spend, typed
                raise PostEngineFailure(cause=exc, spend=spend.spend) from exc

        # Flight recorder capture [EVAL-24 AC-1] — reasoning is a SEPARATE artifact
        # from the graded trajectory, from the same already-scrubbed native log,
        # through the same redaction door. No reasoning → None: no artifact, no
        # sha (honest absence [AC-4]); a corrupt/unwritable recorder fails closed.
        reasoning = (
            adapter.normalize_reasoning(native_log) if native_log is not None else None
        )
        if reasoning is not None:
            try:
                flight_recorder_sha = persist_flight_recorder(
                    FlightRecorder(trial_id=trial_id, platform=arm.platform, entries=reasoning),
                    result.artifacts_dir,
                    extra_patterns,
                )
            except Exception as exc:  # PRA-M8: carry the already-incurred spend, typed
                raise PostEngineFailure(cause=exc, spend=spend.spend) from exc

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
