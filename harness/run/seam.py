"""The run-trial seam [EVAL-4 §M1, AC-1].

``run_trial(task, arm, workspace, config) -> TrialRecord``. The engine is chosen
by config; the seam itself knows nothing of Harbor. It builds the (holdout-free)
request, runs the engine, redacts captured artifacts, normalizes telemetry via
the platform adapter, and assembles the ADVISORY-stamped record. Every deviation
(timeout, infra failure, egress attempt) is recorded as data on the record, never
raised as an exception.
"""

from __future__ import annotations

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
        concurrency=config.concurrency,
        proxy=config.proxy,
        provider_keys=config.provider_keys,
        fake_behavior=task.fake_behavior,
    )

    result = config.engine.run(request)

    # Redact secrets from captured artifacts before they persist [AC-8].
    redact_artifacts(result.artifacts_dir, config.redact_extra_patterns)

    # Normalize telemetry from agent-native logs [AC-2]; unmeasurable ⇒ null.
    adapter = get_adapter(arm.platform)
    telemetry = adapter.normalize(result.native_log)

    flags = Flags(
        egress_violation=result.egress_violation,
        contention_caveat=config.concurrency > 1,  # [D003]
    )
    if result.egress_attempts:
        flags.egress_attempts = result.egress_attempts
    if result.proxy_metered_cost is not None and telemetry.cost is not None:
        # surface the cross-check delta; do NOT reconcile [risks §10]
        flags.proxy_cost_delta = round(result.proxy_metered_cost - telemetry.cost, 6)

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
