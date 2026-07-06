"""Judge a reused control against a fresh contender [control-reuse plan, slice 5].

The judge already operates over *stored* responses, so reusing a control here is
assembly, not a new judging path: pair each fresh contender trial with the reused
control trial per ``(task, repetition)`` — contender diff read live, control diff
read from the snapshot stashed at import, holdouts from each side's grade — and
run the identical identity-blind, order-debiased :func:`judge_pair`, recording a
``reused_judge_verdict`` (distinct from the native kind, so official
judge_preference / calibration never see it). Exploratory-only by construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..corpus.public import content_sha
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import find_events, latest_event
from ..run.control_reuse import ControlReuseError, primary_pair_contender
from ..run.reuse import reused_diff_path
from .assemble import (
    Comparison,
    _holdout_results,
    comparison_id_for,
    read_workspace_diff,
)
from .packet import ResponseArtifacts


def reused_control_arm(ledger_path) -> str | None:
    """The control arm reused into this ledger, or None if no import happened."""
    ev = latest_event(ledger_path, events.CONTROL_REUSED)
    return ev["control_arm"] if ev is not None else None


def _reused_from(ledger_path) -> dict:
    ev = latest_event(ledger_path, events.CONTROL_REUSED)
    return {
        "source_experiment_id": ev["source_experiment_id"],
        "bundle_sha256": ev["bundle_sha256"],
    }


def comparisons_from_reuse(ledger_path, experiment_dir, spec, *, task_classes=None) -> list[Comparison]:
    """Pair the fresh contender arm against the reused control per (task, rep).

    Returns [] when no control was reused, or when the reused arm is not part of
    the pre-registered primary pair (``spec.arms[0..1]``) — >2-arm reuse judging
    is out of scope for v1. ``arm_map`` keeps the spec's A/B order so a reused
    verdict is frame-correct exactly like a native one.
    """
    task_classes = task_classes or {}
    control_arm = reused_control_arm(ledger_path)
    if control_arm is None:
        return []
    contender_arm = primary_pair_contender(spec, control_arm)
    if contender_arm is None:
        return []
    arm_a, arm_b = spec.arms[0], spec.arms[1]
    experiment_dir = Path(experiment_dir)

    reused_trial_events = find_events(ledger_path, events.REUSED_TRIAL)
    reused_trials = {
        (e["trial_record"]["task_id"], e["trial_record"]["repetition"]): e["trial_record"]
        for e in reused_trial_events
    }
    # diff snapshots live beside the ledger, outside the hash chain — their sha
    # was recorded on the reused_trial event at import so the judge can verify the
    # bytes it reads are the ones that were imported (fail loudly on tamper).
    reused_diff_sha = {
        e["trial_record"]["trial_id"]: e.get("diff_sha256") for e in reused_trial_events
    }
    reused_grades = {
        e["grade"]["trial_id"]: e["grade"] for e in find_events(ledger_path, events.REUSED_GRADE)
    }
    native_trials = {
        (e["trial_record"]["task_id"], e["trial_record"]["repetition"]): e["trial_record"]
        for e in find_events(ledger_path, events.TRIAL)
        if e["trial_record"]["arm"] == contender_arm
    }
    native_grades = {g["trial_id"]: g for g in find_events(ledger_path, events.GRADE)}

    def _control(task_id, rep) -> ResponseArtifacts:
        tr = reused_trials[(task_id, rep)]
        trial_id = tr["trial_id"]
        path = reused_diff_path(experiment_dir, trial_id)
        if not path.exists():
            raise ControlReuseError(
                f"reused control diff snapshot missing for trial {trial_id} at "
                f"{path}; the control bundle must be imported into this experiment "
                "dir (bench run --reuse-control) before judging"
            )
        diff = path.read_text(encoding="utf-8")
        recorded = reused_diff_sha.get(trial_id)
        if recorded is not None and content_sha(diff) != recorded:
            raise ControlReuseError(
                f"reused control diff snapshot for trial {trial_id} does not match "
                "its recorded diff_sha256 — the snapshot was modified after import; "
                "refusing to judge tampered evidence"
            )
        return ResponseArtifacts(
            diff=diff, holdout_results=_holdout_results(reused_grades.get(trial_id))
        )

    def _contender(task_id, rep) -> ResponseArtifacts:
        tr = native_trials[(task_id, rep)]
        return ResponseArtifacts(
            diff=read_workspace_diff(tr.get("artifacts_path")),
            holdout_results=_holdout_results(native_grades.get(tr["trial_id"])),
        )

    out: list[Comparison] = []
    for task_id, rep in sorted(set(reused_trials) & set(native_trials)):
        if control_arm == arm_a.name:
            resp_a, resp_b = _control(task_id, rep), _contender(task_id, rep)
        else:
            resp_a, resp_b = _contender(task_id, rep), _control(task_id, rep)
        out.append(
            Comparison(
                comparison_id=comparison_id_for(task_id, rep),
                task_id=task_id,
                repetition=rep,
                task_class=task_classes.get(task_id, "default"),
                arm_map={"A": arm_a.name, "B": arm_b.name},
                response_a=resp_a,
                response_b=resp_b,
            )
        )
    return out


def judge_reused(
    ledger_path,
    experiment_dir,
    spec,
    ctx: EventContext,
    *,
    rubric: str,
    prompts: dict,
    canaries: list,
    task_classes: dict,
    ceiling: Optional[int] = None,
    accumulated: int = 0,
) -> int:
    """Judge every reused (contender vs reused-control) comparison, recording one
    ``reused_judge_verdict`` each. Returns the number newly judged; no-op when
    nothing was reused.

    Idempotent: skips comparisons that already carry a NON-transient reused
    verdict, so a transient CANT_JUDGE (timeout / provider_error) is retried on a
    re-run — the shared :class:`JudgingSession` semantics [refactor 05 §4].

    Honors the same locked judge token ceiling as native judging [F-M-J3]:
    ``accumulated`` seeds the budget with prior spend (native + reused verdicts,
    so a re-run cannot reset it) and reused verdicts count against it — reuse
    cannot spend past the pre-registered cap. A refuse-to-start at the ceiling,
    like the cost guard."""
    from .session import JudgingSession, VerdictSink

    comparisons = comparisons_from_reuse(ledger_path, experiment_dir, spec, task_classes=task_classes)
    if not comparisons:
        return 0
    reused_from = _reused_from(ledger_path)

    def _append(lp, c, *, verdict):
        return events.append_reused_verdict(lp, c, verdict=verdict, reused_from=reused_from)

    # Same loop as native judging, aimed at the exploratory ``reused_judge_verdict``
    # kind through the reuse-provenance writer [refactor 05 §4].
    session = JudgingSession(
        ledger_path, ctx,
        config=spec.judge, rubric=rubric, prompts=prompts,
        canaries=canaries, ceiling=ceiling,
    )
    sink = VerdictSink(kind=events.REUSED_JUDGE_VERDICT, append_verdict_fn=_append)
    return session.run(comparisons, sink, accumulated=accumulated).judged


# --- one-event property registration ----------------------------------------
def _reused_verdict_entrypoint(ctx_dir: str) -> None:
    import json

    from ..schema.judge_config import JudgeConfig
    from .client import judge_pair
    from .packet import ResponseArtifacts, build_packet
    from .providers.fake import FakeProvider

    d = Path(ctx_dir)
    packet = build_packet(
        ResponseArtifacts(diff="diff a", holdout_results=[{"id": "h1", "result": "pass"}]),
        ResponseArtifacts(diff="diff b", holdout_results=[{"id": "h1", "result": "fail"}]),
        task_prompt="do the task",
        rubric="judge on correctness",
    )
    config = JudgeConfig(
        model="google/gemini-1.5-pro-002", rubric="rubrics/code-task-v1.md",
        orders="both", temperature=0.0,
    )
    v1 = json.dumps({"winner": "1", "reason": "x",
                     "evidence": [{"kind": "diff", "response": 1, "hunk": "@@"}], "confidence": 0.9})
    v2 = json.dumps({"winner": "2", "reason": "x",
                     "evidence": [{"kind": "diff", "response": 2, "hunk": "@@"}], "confidence": 0.9})
    reused_from = {"source_experiment_id": "src", "bundle_sha256": "sha"}
    judge_pair(
        packet, config, d / "ledger.ndjson", EventContext(experiment_id="prop"),
        ts="t0", provider=FakeProvider([v1, v2]),
        append_verdict_fn=lambda lp, c, *, verdict: events.append_reused_verdict(
            lp, c, verdict=verdict, reused_from=reused_from
        ),
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("reused-judge-verdict", _reused_verdict_entrypoint)


_register()
