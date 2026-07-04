"""The run-trial seam [EVAL-4 §M1, AC-1].

``run_trial(task, arm, workspace, config) -> TrialRecord``. The engine is chosen
by config; the seam itself knows nothing of Harbor. It builds the (holdout-free)
request, runs the engine, redacts captured artifacts, normalizes telemetry via
the platform adapter, and assembles the ADVISORY-stamped record. Every deviation
(timeout, infra failure, egress attempt) is recorded as data on the record, never
raised as an exception.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..adapters import get_adapter
from ..adapters.base import Flags, Outcome, Provenance, TrialRecord
from ..schema.experiment import Arm
from .redact import redact_artifacts
from .types import RunConfig, Task, TrialRequest


class HoldoutLeakError(RuntimeError):
    """A holdout canary reached the prompt payload — insulation breach [AC-9]."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    import json as _json

    prompt = task.prompt
    request_blob = "\n".join(
        [
            prompt,
            _json.dumps(arm.payload, sort_keys=True, default=str),
            _json.dumps(task.fake_behavior, sort_keys=True, default=str),
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

    flags = Flags(egress_violation=result.egress_violation)
    if result.egress_attempts:
        flags.egress_attempts = result.egress_attempts
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
    )
