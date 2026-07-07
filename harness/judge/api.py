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
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import VerdiRefusal


class JudgeRubricError(VerdiRefusal, RuntimeError):
    """The judge rubric is missing, or was swapped after the lock [D-P7-6]."""


@dataclass(frozen=True)
class JudgeOutcome:
    """What ``bench judge`` computed, for the CLI to render in order.

    ``rubric_warning`` flags a legacy lock (pre-rubric-commitment) so the CLI
    emits the same non-fatal warning the body did [D-P7-6]; ``calibration`` is
    the per-class kappa map the summary lines render [JD-9, D006].

    ``judged`` (the native pass) splits into ``verdicts`` (substantive) +
    ``cant_judge`` (fail-closed), with ``cant_judge_reasons`` a ``{reason: count}``
    map [ux-friction AC-3], so the summary discloses a keyless/failing pass
    instead of the success-shaped bare count (F6). Additive with defaults — no
    existing constructor or serializer changes; the reused-control counts stay in
    ``n_reused`` and their line is unchanged.
    """

    judged: int
    stopped_ceiling: bool
    accumulated: int
    ceiling: int | None
    n_reused: int
    rubric_warning: bool
    calibration: dict
    verdicts: int = 0
    cant_judge: int = 0
    cant_judge_reasons: dict = field(default_factory=dict)


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
    from ..ledger.identity import derive_experiment_id
    from ..plan.lock import assert_lock
    from ..review.calibrate import calibration_from_spec
    from .assemble import native_comparisons_from_ledger
    from .session import NATIVE_SINK, JudgingSession

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
    # [ux-friction AC-1] one shared seam: resolve exp_dir before naming.
    ctx = EventContext(experiment_id=derive_experiment_id(exp_dir), actor=resolved_actor)

    comparisons = native_comparisons_from_ledger(ledger_path, spec, task_classes=task_classes)

    # The native pairing and the reused-control pairing share one judging loop
    # [refactor 05 §4]: the session skips comparisons already carrying a
    # non-transient verdict (7A-4; a transient CANT_JUDGE is re-attempted,
    # PRA-M13), honors the locked judge token ceiling (F-M-J3), and appends
    # exactly one verdict per comparison [AC-8].
    ceiling = spec.judge.token_ceiling
    session = JudgingSession(
        ledger_path, ctx,
        config=spec.judge, rubric=rubric, prompts=prompts,
        canaries=canaries, ceiling=ceiling,
    )
    # F-M-J3: resume-aware — seed the budget from BOTH the native and reused
    # verdict kinds' provider-reported usage, so a re-run cannot reset it.
    accumulated = session.seed_accumulated(
        (events.JUDGE_VERDICT, events.REUSED_JUDGE_VERDICT)
    )
    native = session.run(comparisons, NATIVE_SINK, accumulated=accumulated)
    judged, accumulated, stopped_ceiling = (
        native.judged, native.accumulated, native.stopped_ceiling
    )
    # AC-3: the verdict/cant_judge split of the native pass, for the summary line.
    cant_judge_reasons = native.cant_judge_reasons
    cant_judge = sum(cant_judge_reasons.values())

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
        calibration=cal, verdicts=native.verdicts, cant_judge=cant_judge,
        cant_judge_reasons=cant_judge_reasons,
    )
