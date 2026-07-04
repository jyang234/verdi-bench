"""Memory probe: prefix-completion membership probes per arm model [EVAL-10 AC-3, D002].

Two v1 techniques through the EVAL-2 provider client seam (the
``harness.process`` precedent — import, don't fork): **canary regurgitation**
(a model that completes the canary without it in context has seen the task in
training) and **oracle-prefix continuation** (a continuation reproducing the
oracle's remainder above the pre-registered overlap threshold). Probes never
run inside trial containers and share no context with judge calls — each probe
is a fresh, single-message model call from this module [constraint].

Fail-closed: any provider failure yields one ``contamination_probe`` event with
``status: cant_probe`` and a closed-set reason, carrying **no** outcomes —
never a silent partial probe. Canary values are unrepresentable in the event:
hash-only [AC-2].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from ..judge.providers.base import (
    Provider,
    ProviderError,
    get_provider,
    provider_failure_reason,
)
from ..ledger.events import EventContext, record_contamination_probe
from ..schema.experiment import Arm
from .canary import derive_canary, hash_canary
from .overlap import DEFAULT_OVERLAP_THRESHOLD, solution_overlap


class ProbeError(ValueError):
    """A probe-input precondition failed [fail-loudly]."""


class _CanaryInPrompt(Exception):
    """Internal: a probe prompt contains the canary it is meant to detect —
    sending it would manufacture a false positive, so the run fails closed."""


@dataclass(frozen=True)
class ProbeTask:
    """One task's probe inputs. ``prompt`` must be the pre-embed task content
    (the canary is what the model must produce, never what we send); ``oracle``
    is the reference solution when the corpus carries one."""

    task_id: str
    task_sha: str
    prompt: str
    oracle: Optional[str] = None
    has_canary: bool = False


def _probe_messages(instruction: str, body: str) -> list[dict]:
    return [{"role": "user", "content": f"{instruction}\n\n{body}"}]


def run_memory_probe(
    ledger_path,
    ctx: EventContext,
    *,
    arms: Sequence[Arm],
    tasks: Sequence[ProbeTask],
    provider: Optional[Provider] = None,
    threshold: Optional[float] = None,
    overlap_flags: Optional[Mapping[str, Mapping[str, bool]]] = None,
) -> dict:
    """Probe every arm model for training-set membership of ``tasks`` [AC-3].

    Ledgers exactly one ``contamination_probe`` event per run and returns it.
    Per (arm, task) the outcome is a tri-state: ``flagged`` (a positive
    detection on any channel), ``negative`` (probed/scanned, nothing detected),
    or ``unprobed`` (no canary, no oracle, no overlap scan — nothing to
    measure). ``overlap_flags`` merges the deterministic AC-4 channel's
    per-(arm, task) scan results into the same event, so one record carries the
    whole measurement; unknown arm/task keys are refused. Any
    :class:`ProviderError` fails the whole run closed to ``cant_probe`` with
    the shared closed-set reason — outcomes from arms already probed are
    discarded, never partially reported.
    """
    if not arms:
        raise ProbeError("no arms to probe; a probe run needs at least one arm")
    if not tasks:
        raise ProbeError("no tasks to probe; a probe run needs at least one task")
    resolved_threshold = (
        DEFAULT_OVERLAP_THRESHOLD if threshold is None else threshold
    )
    overlap_flags = overlap_flags or {}
    arm_names = {a.name for a in arms}
    task_ids = {t.task_id for t in tasks}
    for arm_name, per_task in overlap_flags.items():
        if arm_name not in arm_names:
            raise ProbeError(f"overlap_flags names unknown arm {arm_name!r}")
        unknown = sorted(set(per_task) - task_ids)
        if unknown:
            raise ProbeError(
                f"overlap_flags for arm {arm_name!r} names unknown task(s) {unknown}"
            )

    def _probe_all() -> dict:
        arms_out: dict[str, dict] = {}
        for arm in arms:
            prov = provider if provider is not None else get_provider(arm.model)
            outcomes: dict[str, str] = {}
            evidence: dict[str, list[str]] = {}
            for task in tasks:
                hits: list[str] = []
                measured = False
                if task.has_canary:
                    canary = derive_canary(task.task_sha)
                    messages = _probe_messages(
                        "Continue this task description exactly as it appears "
                        "in your training data:",
                        task.prompt,
                    )
                    if canary in messages[0]["content"]:
                        raise _CanaryInPrompt(task.task_id)
                    completion = prov.complete(arm.model, messages, 0.0)
                    measured = True
                    if canary in completion:
                        hits.append("canary_regurgitation")
                if task.oracle is not None:
                    mid = len(task.oracle) // 2
                    prefix, remainder = task.oracle[:mid], task.oracle[mid:]
                    completion = prov.complete(
                        arm.model,
                        _probe_messages(
                            "Continue this solution exactly, byte for byte:",
                            prefix,
                        ),
                        0.0,
                    )
                    measured = True
                    continuation = solution_overlap(
                        completion, oracle=remainder, threshold=resolved_threshold
                    )
                    if continuation.flagged:
                        hits.append("oracle_prefix")
                scanned = overlap_flags.get(arm.name, {})
                if task.task_id in scanned:
                    measured = True
                    if scanned[task.task_id]:
                        hits.append("solution_overlap")
                if hits:
                    outcomes[task.task_id] = "flagged"
                elif measured:
                    outcomes[task.task_id] = "negative"
                else:
                    outcomes[task.task_id] = "unprobed"
                evidence[task.task_id] = hits
            arms_out[arm.name] = {
                "model": arm.model,
                "outcomes": outcomes,
                "evidence": evidence,
            }
        return arms_out

    try:
        arms_out = _probe_all()
    except ProviderError as e:
        probe = {
            "status": "cant_probe",
            "reason": provider_failure_reason(e),
            "threshold": resolved_threshold,
        }
        return record_contamination_probe(ledger_path, ctx, probe=probe)
    except _CanaryInPrompt as e:
        probe = {
            "status": "cant_probe",
            "reason": "canary_in_prompt",
            "threshold": resolved_threshold,
            "task_id": e.args[0],
        }
        return record_contamination_probe(ledger_path, ctx, probe=probe)

    probe = {
        "status": "complete",
        "reason": None,
        "threshold": resolved_threshold,
        "arms": arms_out,
        # hash-only: the canary value is a secret of the instrument [AC-2]
        "canary_sha256": {
            t.task_id: hash_canary(derive_canary(t.task_sha))
            for t in tasks
            if t.has_canary
        },
    }
    return record_contamination_probe(ledger_path, ctx, probe=probe)


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
def _probe_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from ..judge.providers.fake import FakeProvider

    run_memory_probe(
        Path(ctx_dir) / "ledger.ndjson",
        EventContext(experiment_id="prop"),
        arms=[Arm(name="prop-arm", platform="claude_code", model="fake/prop-model")],
        tasks=[
            ProbeTask(
                task_id="t-prop", task_sha="c3" * 32,
                prompt="refactor the widget loader carefully", has_canary=True,
            )
        ],
        provider=FakeProvider(["nothing memorized here"]),
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("contamination-probe", _probe_entrypoint)


_register()
