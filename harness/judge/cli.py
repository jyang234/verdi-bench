"""``bench judge`` [EVAL-2 §M5, JD-9].

Asserts the experiment lock and the task-content commitment, pairs the graded
trials per ``(task, repetition)``, and judges each comparison — appending exactly
one ``judge_verdict`` per comparison [AC-8]. Canaries are derived from the
**locked** spec (arm names, platforms, model ids), so the identity firewall is
fed from the pre-registered contract [AC-2]. The ``EscalationConfig`` from the
locked ``judge.escalation`` block drives the per-class kappa summary [AC-7, D006].

The judge model is the locked ``judge.model``; a ``fake/...`` prefix selects the
deterministic no-network judge for a fake-engine experiment (the judge analog of
``--engine fake``).
"""

from __future__ import annotations

from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    @app.command()
    def judge(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
    ) -> None:
        """Judge every graded comparison; append one verdict each."""
        from ..blind.core import arm_canaries
        from ..corpus.commit import (
            TaskCommitmentError,
            assert_task_commitment,
            load_task_dicts,
        )
        from ..ledger import events
        from ..ledger.events import EventContext
        from ..ledger.query import find_events
        from ..plan.lock import assert_lock
        from ..schema.experiment import ExperimentSpec
        from ..review.calibrate import calibration_from_spec
        from .assemble import comparisons_from_ledger
        from .client import judge_pair
        from .packet import build_packet

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        lock_event = assert_lock(spec_path, ledger_path)
        spec = ExperimentSpec.from_yaml(spec_path)

        task_dicts = load_task_dicts(experiment_dir)
        # Refuse tasks swapped after the lock before judging anything [PL-7/D-6].
        try:
            assert_task_commitment(
                lock_event, task_dicts,
                corpus_id=spec.corpus.id, semver=spec.corpus.version,
            )
        except TaskCommitmentError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)

        rubric_path = experiment_dir / spec.judge.rubric
        if not rubric_path.is_file():
            typer.echo(
                f"judge rubric {spec.judge.rubric!r} not found at {rubric_path}", err=True
            )
            raise typer.Exit(code=2)
        rubric = rubric_path.read_text(encoding="utf-8")

        task_classes = {t["id"]: t.get("task_class", "default") for t in task_dicts}
        prompts = {t["id"]: t.get("prompt", "") for t in task_dicts}
        canaries = arm_canaries(spec.arms)
        ctx = EventContext(experiment_id=experiment_dir.name)

        comparisons = comparisons_from_ledger(ledger_path, spec, task_classes=task_classes)
        # 7A-4: idempotent — one verdict per comparison. A re-run must not append
        # a duplicate verdict set (which would inflate calibration/preference
        # statistics); skip comparisons that already carry a judge_verdict,
        # mirroring process score's `already` skip.
        already = {
            ev["verdict"]["comparison_id"]
            for ev in find_events(ledger_path, events.JUDGE_VERDICT)
        }
        judged = 0
        for cmp in comparisons:
            if cmp.comparison_id in already:
                continue
            packet = build_packet(
                cmp.response_a, cmp.response_b,
                task_prompt=prompts.get(cmp.task_id, ""),
                rubric=rubric,
            )
            judge_pair(
                packet, spec.judge, ledger_path, ctx,
                ts=ctx.clock(), canaries=canaries,
                comparison_id=cmp.comparison_id, task_class=cmp.task_class,
                arm_map=cmp.arm_map, task_id=cmp.task_id,
            )
            judged += 1
        typer.echo(f"judged {judged} comparison(s)")

        # Thread the locked EscalationConfig through calibration [JD-9, D006]:
        # per-class kappa against any human verdicts, through the D003 IPW seam
        # (not raw pooled kappa over the disagreement-heavy reviewed set) [RV-4].
        # The same seam feeds the analyze render, so the two can't drift. Empty
        # until human review exists — the real escalation table rides analyze.
        cal = calibration_from_spec(ledger_path, spec, spec.seed)
        for cls in sorted(cal):
            c = cal[cls]
            if not c.sufficient:
                typer.echo(f"  class {cls}: n={c.n} (insufficient for kappa)")
            else:
                flag = " ESCALATE" if c.escalate else ""
                typer.echo(f"  class {cls}: n={c.n} kappa={c.kappa:.3f}{flag}")
