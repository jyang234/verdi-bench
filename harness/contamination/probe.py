"""Memory probe: prefix-completion membership probes per arm model [EVAL-10 AC-3, D002].

Two v1 techniques through the EVAL-2 provider client seam (the
``harness.process`` precedent — import, don't fork): **canary regurgitation**
(a model that completes the canary without it in context has seen the task in
training) and **oracle-prefix continuation** (a continuation reproducing the
oracle's remainder above the pre-registered overlap threshold). Probes never
run inside trial containers and share no context with judge calls — each probe
is a fresh, single-message model call from this module [constraint].

Fail-closed: deterministic input problems (a canary already in a probe prompt,
an oracle too short to compare) are refused **before any provider call**, and
any provider failure yields one ``contamination_probe`` event with
``status: cant_probe`` and a closed-set reason, carrying no per-task LLM
outcomes — never a silent partial probe. The deterministic AC-4 overlap flags
passed in by the caller ride *every* event, complete or not: an unrelated
provider outage must not erase evidence already computed from disk. Canary
values are unrepresentable in the event: hash-only [AC-2].
"""

from __future__ import annotations

import keyword
import re

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
from .canary import derive_canary, hash_canary, strip_canary
from .overlap import DEFAULT_OVERLAP_THRESHOLD, fingerprintable, solution_overlap


class ProbeError(ValueError):
    """A probe-input precondition failed [fail-loudly]."""


@dataclass(frozen=True)
class ProbeTask:
    """One task's probe inputs. ``prompt`` is the task content as materialized
    (an embedded canary marker is stripped before probing — the canary is what
    the model must produce, never what we send); ``oracle`` is the reference
    solution when the corpus carries one."""

    task_id: str
    task_sha: str
    prompt: str
    oracle: Optional[str] = None
    has_canary: bool = False


def _probe_messages(instruction: str, body: str) -> list[dict]:
    return [{"role": "user", "content": f"{instruction}\n\n{body}"}]


def _canary_probe_body(task: ProbeTask) -> str:
    """The canary-probe prompt body: the task content without its marker."""
    return strip_canary(task.prompt, derive_canary(task.task_sha))


# F-M-C2 (approved): the oracle-prefix channel flags only when the TRUE
# prefix outperforms a perturbed CONTROL prefix by at least this margin.
# Without a control, formulaic code a clean model can legitimately continue
# tripped the >=threshold reconstruction test — and one false positive is
# asymmetric, refusing the official render.
ORACLE_CONTROL_MARGIN = 0.2

_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")
_CONTROL_STOPWORDS = frozenset(keyword.kwlist) | {
    "self", "None", "True", "False", "print", "return", "import", "from",
}


def perturb_identifiers(text: str) -> str:
    """The control-condition transform [F-M-C2]: rename identifiers to
    position-derived names (first-appearance order — pure and deterministic,
    no randomness). Renaming breaks the verbatim-recall key a memorizer needs
    while preserving the surface structure a clean model continues from; the
    control completion is scored against the identically-perturbed remainder,
    so a formulaic continuer scores HIGH in both conditions (margin ~ 0) and
    only genuine memorization lifts the true condition above the control."""
    mapping: dict[str, str] = {}

    def _sub(m: "re.Match[str]") -> str:
        tok = m.group(0)
        if tok in _CONTROL_STOPWORDS:
            return tok
        if tok not in mapping:
            mapping[tok] = f"qv{len(mapping)}"
        return mapping[tok]

    return _IDENT_RE.sub(_sub, text)


def _split_oracle(oracle: str) -> tuple[str, str]:
    """Token-boundary prefix/remainder split — a character midpoint would
    bisect a token and corrupt both halves' fingerprints."""
    words = oracle.split()
    mid = len(words) // 2
    return " ".join(words[:mid]), " ".join(words[mid:])


def _preflight(tasks: Sequence[ProbeTask]) -> Optional[dict]:
    """Deterministic input validation before any provider call [fail-closed].

    Returns the ``cant_probe`` payload core for the first unusable input, or
    None when every task is probeable: a canary surviving outside its marker
    would manufacture a false positive, and an oracle whose remainder cannot
    be fingerprinted would crash mid-run after burning provider calls.
    """
    for task in tasks:
        if task.has_canary:
            canary = derive_canary(task.task_sha)
            if canary in _canary_probe_body(task):
                return {"reason": "canary_in_prompt", "task_id": task.task_id}
        if task.oracle is not None:
            prefix, remainder = _split_oracle(task.oracle)
            if not prefix or not fingerprintable(remainder):
                return {"reason": "oracle_unfingerprintable", "task_id": task.task_id}
            # F-M-C2: the control condition must be measurable too, checked
            # before any provider call is burned.
            c_prefix, c_remainder = _split_oracle(perturb_identifiers(task.oracle))
            if not c_prefix or not fingerprintable(c_remainder):
                return {"reason": "oracle_unfingerprintable", "task_id": task.task_id}
    return None


def run_memory_probe(
    ledger_path,
    ctx: EventContext,
    *,
    arms: Sequence[Arm],
    tasks: Sequence[ProbeTask],
    provider: Optional[Provider] = None,
    threshold: Optional[float] = None,
    overlap_flags: Optional[Mapping[str, Mapping[str, bool]]] = None,
    alarms: Optional[Sequence[str]] = None,
    skipped: Optional[Sequence[str]] = None,
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
    the shared closed-set reason — LLM outcomes from arms already probed are
    discarded, never partially reported, while the deterministic
    ``overlap_flags`` still ride the event.
    """
    if not arms:
        raise ProbeError("no arms to probe; a probe run needs at least one arm")
    if not tasks:
        raise ProbeError("no tasks to probe; a probe run needs at least one task")
    resolved_threshold = (
        DEFAULT_OVERLAP_THRESHOLD if threshold is None else threshold
    )
    overlap_flags = {a: dict(m) for a, m in (overlap_flags or {}).items()}
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

    base = {"threshold": resolved_threshold, "overlap_flags": overlap_flags}
    # F-M-C3 (additive): the scan's insulation alarms and unscanned trials ride
    # the SAME event as the overlap flags — previously stderr-only, so a
    # holdout-leak breach or a wiped-workspace UNSCANNED trial evaporated and
    # was indistinguishable from scanned-clean in every downstream summary.
    if alarms is not None:
        base["alarms"] = list(alarms)
    if skipped is not None:
        base["skipped"] = list(skipped)
    refused = _preflight(tasks)
    if refused is not None:
        probe = {"status": "cant_probe", **refused, **base}
        return record_contamination_probe(ledger_path, ctx, probe=probe)

    try:
        arms_out: dict[str, dict] = {}
        for arm in arms:
            prov = provider if provider is not None else get_provider(arm.model)
            oracle_scores: dict[str, dict] = {}
            outcomes: dict[str, str] = {}
            evidence: dict[str, list[str]] = {}
            for task in tasks:
                hits: list[str] = []
                measured = False
                if task.has_canary:
                    canary = derive_canary(task.task_sha)
                    completion = prov.complete(
                        arm.model,
                        _probe_messages(
                            "Continue this task description exactly as it "
                            "appears in your training data:",
                            _canary_probe_body(task),
                        ),
                        0.0,
                    )
                    measured = True
                    if canary in completion:
                        hits.append("canary_regurgitation")
                if task.oracle is not None:
                    prefix, remainder = _split_oracle(task.oracle)
                    completion = prov.complete(
                        arm.model,
                        _probe_messages(
                            "Continue this solution exactly, byte for byte:",
                            prefix,
                        ),
                        0.0,
                    )
                    # F-M-C2: the CONTROL condition — the same ask over the
                    # identifier-perturbed prefix, scored against the
                    # identically-perturbed remainder. Doubles the provider
                    # calls per (arm, oracle-task); the disclosed cost of a
                    # false-positive channel that refuses official renders.
                    c_prefix, c_remainder = _split_oracle(
                        perturb_identifiers(task.oracle)
                    )
                    control_completion = prov.complete(
                        arm.model,
                        _probe_messages(
                            "Continue this solution exactly, byte for byte:",
                            c_prefix,
                        ),
                        0.0,
                    )
                    measured = True
                    continuation = solution_overlap(
                        completion, oracle=remainder, threshold=resolved_threshold
                    )
                    control = solution_overlap(
                        control_completion, oracle=c_remainder,
                        threshold=resolved_threshold,
                    )
                    true_score = continuation.oracle_score or 0.0
                    control_score = control.oracle_score or 0.0
                    margin = round(true_score - control_score, 4)
                    oracle_scores[task.task_id] = {
                        "true": round(true_score, 4),
                        "control": round(control_score, 4),
                        "margin": margin,
                    }
                    if continuation.flagged and margin >= ORACLE_CONTROL_MARGIN:
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
            if oracle_scores:  # additive [F-M-C2]: true/control/margin per task
                arms_out[arm.name]["oracle_scores"] = oracle_scores
    except ProviderError as e:
        probe = {
            "status": "cant_probe",
            "reason": provider_failure_reason(e),
            **base,
        }
        return record_contamination_probe(ledger_path, ctx, probe=probe)

    probe = {
        "status": "complete",
        "reason": None,
        **base,
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
