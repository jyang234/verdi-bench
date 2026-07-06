"""``judge`` stage API [refactor 02 §3].

The importable entry point behind ``bench judge`` [EVAL-2 §M5, JD-9]: assert the
lock and the task-content commitment, pair the graded trials per
``(task, repetition)``, and judge each comparison — appending exactly one
``judge_verdict`` per comparison [AC-8]. Canaries derive from the **locked** spec
(arm names, platforms, model ids), so the identity firewall is fed from the
pre-registered contract [AC-2]. The typer verb (``harness/judge/cli.py``) is a
thin shell that maps the refusals to exit codes and renders the per-class kappa
summary from the returned :class:`JudgeOutcome`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class JudgeRubricError(RuntimeError):
    """The judge rubric is missing, or was swapped after the lock [D-P7-6]."""


@dataclass(frozen=True)
class JudgeOutcome:
    """What ``bench judge`` computed, for the CLI to render in order.

    ``rubric_warning`` flags a legacy lock (pre-rubric-commitment) so the CLI
    emits the same non-fatal warning the body did [D-P7-6]; ``calibration`` is
    the per-class kappa map the summary lines render [JD-9, D006].
    """

    judged: int
    stopped_ceiling: bool
    accumulated: int
    ceiling: int | None
    n_reused: int
    rubric_warning: bool
    calibration: dict


def judge_experiment(exp_dir: Path, *, actor: str | None = None) -> JudgeOutcome:
    """Judge every graded comparison; append one verdict each [EVAL-2 §M5].

    Raises the enumerated refusals the CLI maps to exit 2 —
    ``TaskCommitmentError`` (task swapped post-lock), ``JudgeRubricError``
    (rubric missing or swapped), ``ActorResolutionError`` — and otherwise returns
    the counts + calibration the CLI renders.
    """
    from ..blind.core import arm_canaries
    from ..corpus.commit import assert_task_commitment, load_task_dicts
    from ..ledger import events
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from ..ledger.query import find_events
    from ..plan.lock import assert_lock
    from ..review.calibrate import calibration_from_spec
    from .assemble import comparisons_from_ledger
    from .client import judge_pair
    from .packet import build_packet
    from .schema import TRANSIENT_CANT_JUDGE

    exp_dir = Path(exp_dir)
    spec_path = exp_dir / "experiment.yaml"
    ledger_path = exp_dir / "ledger.ndjson"
    _lock = assert_lock(spec_path, ledger_path)
    lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read

    task_dicts = load_task_dicts(exp_dir)
    # Refuse tasks swapped after the lock before judging anything [PL-7/D-6].
    assert_task_commitment(
        lock_event, task_dicts,
        corpus_id=spec.corpus.id, semver=spec.corpus.version,
    )

    rubric_path = exp_dir / spec.judge.rubric
    if not rubric_path.is_file():
        raise JudgeRubricError(
            f"judge rubric {spec.judge.rubric!r} not found at {rubric_path}"
        )
    rubric = rubric_path.read_text(encoding="utf-8")

    # D-P7-6: refuse a rubric swapped after the lock. The on-disk rubric's
    # normalized-text hash must equal the lock's committed rubric_sha256. A legacy
    # lock (no field) warns instead of refusing — a pre-Phase-7 chain is never
    # invalidated.
    rubric_sha = hashlib.sha256(rubric.encode("utf-8")).hexdigest()
    locked_rubric_sha = lock_event.get("rubric_sha256")
    rubric_warning = locked_rubric_sha is None
    if not rubric_warning and rubric_sha != locked_rubric_sha:
        raise JudgeRubricError(
            f"judge rubric {spec.judge.rubric!r} was swapped after the lock:\n"
            f"  locked   rubric_sha256: {locked_rubric_sha}\n"
            f"  on-disk  rubric_sha256: {rubric_sha}\n"
            "the judging rubric is immutable post-lock [D-P7-6]"
        )

    task_classes = {t["id"]: t.get("task_class", "default") for t in task_dicts}
    prompts = {t["id"]: t.get("prompt", "") for t in task_dicts}
    canaries = arm_canaries(spec.arms)
    # F-L1/GR-12: the ledgered actor is resolved (flag, else OS user) and REFUSED
    # when unresolvable — never silently defaulted to "local".
    resolved_actor = resolve_actor(actor)
    ctx = EventContext(experiment_id=exp_dir.name, actor=resolved_actor)

    comparisons = comparisons_from_ledger(ledger_path, spec, task_classes=task_classes)

    # 7A-4: idempotent — one verdict per comparison; skip comparisons that already
    # carry a verdict. PRA-M13: a *transient* CANT_JUDGE (the judge could not run)
    # is NOT counted as done, so a re-run re-attempts it; a terminal CANT_JUDGE
    # stays skipped.
    def _is_transient(v: dict) -> bool:
        return v.get("winner") == "CANT_JUDGE" and v.get("reason") in TRANSIENT_CANT_JUDGE

    already = {
        ev["verdict"]["comparison_id"]
        for ev in find_events(ledger_path, events.JUDGE_VERDICT)
        if not _is_transient(ev["verdict"])
    }
    # F-M-J3: the judge-scoped token ceiling (locked spec) — resume-aware: prior
    # verdicts' provider-reported usage seeds the accumulator, so a re-run cannot
    # reset the budget. Seed from BOTH native and reused verdicts.
    ceiling = spec.judge.token_ceiling

    def _verdict_tokens(v: dict) -> int:
        u = (v.get("provenance") or {}).get("usage") or {}
        return int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)

    accumulated = sum(
        _verdict_tokens(ev["verdict"])
        for kind in (events.JUDGE_VERDICT, events.REUSED_JUDGE_VERDICT)
        for ev in find_events(ledger_path, kind)
    )
    stopped_ceiling = False
    judged = 0
    for cmp in comparisons:
        if cmp.comparison_id in already:
            continue
        if ceiling is not None and accumulated >= ceiling:
            events.record_judge_stopped_token_ceiling(
                ledger_path, ctx,
                accumulated_tokens=accumulated, ceiling=ceiling,
            )
            stopped_ceiling = True
            break
        packet = build_packet(
            cmp.response_a, cmp.response_b,
            task_prompt=prompts.get(cmp.task_id, ""),
            rubric=rubric,
        )
        verdict = judge_pair(
            packet, spec.judge, ledger_path, ctx,
            ts=ctx.clock(), canaries=canaries,
            comparison_id=cmp.comparison_id, task_class=cmp.task_class,
            arm_map=cmp.arm_map, task_id=cmp.task_id,
        )
        usage = verdict.provenance.usage or {}
        accumulated += int(usage.get("input_tokens") or 0) + int(
            usage.get("output_tokens") or 0
        )
        judged += 1

    # Control reuse [control-reuse plan]: also judge each fresh-contender vs
    # reused-control pair (a distinct kind the official judge_preference never
    # reads). Exploratory-only, but it draws on the SAME locked judge token
    # budget — skip it entirely once the ceiling has stopped native judging, and
    # thread the running total through so reuse cannot spend past the cap [F-M-J3].
    n_reused = 0
    if not stopped_ceiling:
        from .reuse import judge_reused

        n_reused = judge_reused(
            ledger_path, exp_dir, spec, ctx,
            rubric=rubric, prompts=prompts, canaries=canaries,
            task_classes=task_classes, ceiling=ceiling, accumulated=accumulated,
        )

    # Thread the locked EscalationConfig through calibration [JD-9, D006]: per-class
    # kappa against any human verdicts, through the D003 IPW seam [RV-4]. The same
    # seam feeds the analyze render, so the two can't drift.
    cal = calibration_from_spec(ledger_path, spec, spec.seed)
    return JudgeOutcome(
        judged=judged, stopped_ceiling=stopped_ceiling, accumulated=accumulated,
        ceiling=ceiling, n_reused=n_reused, rubric_warning=rubric_warning,
        calibration=cal,
    )
