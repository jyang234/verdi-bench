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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..adapters import get_adapter
from ..adapters.base import Flags, Outcome, Provenance, TrialRecord
from ..adapters.generic import by_model_delta, normalize_generic_by_model
from ..schema.experiment import Arm
from .redact import redact_artifacts
from .trajectory import TrajectoryCorruptError, TrajectoryRecord, persist_trajectory
from .types import RunConfig, Task, TrialRequest


class HoldoutLeakError(RuntimeError):
    """A holdout canary reached the prompt payload — insulation breach [AC-9]."""


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
        ]
    )
    for canary in task.holdout_canaries:
        if canary and canary in request_blob:
            raise HoldoutLeakError(
                f"holdout canary {canary!r} present in request payload for {task.id}"
            )

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
        provider_keys=config.provider_keys,
        fake_behavior=task.fake_behavior,
    )

    result = config.engine.run(request)

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
    redact_artifacts(workspace, extra_patterns)

    # Normalize telemetry from agent-native logs [AC-2]; unmeasurable ⇒ null.
    adapter = get_adapter(arm.platform)
    telemetry = adapter.normalize(result.native_log)

    # Per-model attribution [EVAL-14 AC-2, AC-4]: a v2 generic log may split
    # telemetry across the arm's DECLARED models. Self-reported testimony, so
    # it rides flags (the advisory channel) — the authoritative whole-trial
    # telemetry above is untouched, and a sum/total mismatch is surfaced as a
    # delta, never reconciled (the proxy_cost_delta precedent). None for v1,
    # non-verdi, and native-platform logs — honest absence.
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
    if result.outcome != Outcome.infra_failed:
        try:
            native_log = _redacted_native_log(Path(result.artifacts_dir), trial_id)
        except TrajectoryCorruptError:
            if result.outcome != Outcome.timeout:
                raise
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
            trajectory_sha = persist_trajectory(
                TrajectoryRecord(trial_id=trial_id, platform=arm.platform, steps=steps),
                result.artifacts_dir,
                extra_patterns,
            )

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
    # Egress attestation [EVAL-13 AC-6, D003]: an ALLOWED host attributable to
    # neither this arm's declared model_hosts nor the shared infra_hosts is
    # flagged (advisory — rides the record, never gates, never fails the trial;
    # the proxy-metered-cost trust pattern). Engages only when this arm opted
    # into declaration (non-empty model_hosts) — an undeclared arm has nothing
    # to attest against, the honest absent state. Denied hosts are already
    # egress_violation; this catches the allowed-but-unattributable case, e.g.
    # an arm reaching the OTHER arm's declared model endpoint.
    if config.proxy is not None and arm.model_hosts and result.egress_attempts:
        attributable = list(config.proxy.infra_hosts)
        for declared in arm.model_hosts.values():
            attributable.extend(declared)
        undeclared = sorted({
            h for h in result.egress_attempts
            if config.proxy.is_allowed(h)
            and not config.proxy.host_matches(h, attributable)
        })
        if undeclared:
            flags.undeclared_model_egress = undeclared
    if result.proxy_metered_cost is not None:
        # Cross-check signal. Surfaced on the record so the cost guard can enforce
        # on it when the arm can't self-report cost (telemetry null) [RN-2] — but
        # it is NEVER written into telemetry.cost: nulls are flagged, not imputed
        # [D004]. When both exist, also surface the delta; do NOT reconcile.
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
    )
