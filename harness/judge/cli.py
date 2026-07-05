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
        actor: str = typer.Option(
            None, "--actor", help="Actor recorded on the verdict events [GR-12]"
        ),
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
        from ..review.calibrate import calibration_from_spec
        from .assemble import comparisons_from_ledger
        from .client import judge_pair
        from .packet import build_packet

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        _lock = assert_lock(spec_path, ledger_path)
        lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read

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

        # D-P7-6: refuse a rubric swapped after the lock. The on-disk rubric's
        # normalized-text hash (the same one the verdict provenance carries) must
        # equal the lock's committed rubric_sha256. A legacy lock (no field) warns
        # instead of refusing — a pre-Phase-7 chain is never invalidated.
        import hashlib

        rubric_sha = hashlib.sha256(rubric.encode("utf-8")).hexdigest()
        locked_rubric_sha = lock_event.get("rubric_sha256")
        if locked_rubric_sha is None:
            typer.echo(
                "WARNING: lock predates rubric commitment (D-P7-6); the rubric "
                "content is not pinned for this experiment", err=True,
            )
        elif rubric_sha != locked_rubric_sha:
            typer.echo(
                f"judge rubric {spec.judge.rubric!r} was swapped after the lock:\n"
                f"  locked   rubric_sha256: {locked_rubric_sha}\n"
                f"  on-disk  rubric_sha256: {rubric_sha}\n"
                "the judging rubric is immutable post-lock [D-P7-6]", err=True,
            )
            raise typer.Exit(code=2)

        task_classes = {t["id"]: t.get("task_class", "default") for t in task_dicts}
        prompts = {t["id"]: t.get("prompt", "") for t in task_dicts}
        canaries = arm_canaries(spec.arms)
        # F-L1/GR-12: the ledgered actor is resolved (flag, else OS user) and
        # REFUSED when unresolvable — never silently defaulted to "local",
        # matching every other ledgering verb and the README's claim.
        from ..ledger.actor import ActorResolutionError, resolve_actor

        try:
            resolved_actor = resolve_actor(actor)
        except ActorResolutionError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        ctx = EventContext(experiment_id=experiment_dir.name, actor=resolved_actor)

        from .schema import TRANSIENT_CANT_JUDGE

        comparisons = comparisons_from_ledger(ledger_path, spec, task_classes=task_classes)
        # 7A-4: idempotent — one verdict per comparison. A re-run must not append
        # a duplicate verdict set (which would inflate calibration/preference
        # statistics); skip comparisons that already carry a judge_verdict,
        # mirroring process score's `already` skip.
        # PRA-M13: a *transient* CANT_JUDGE (timeout / provider_error — the judge
        # could not run) is NOT counted as done, so a re-run re-attempts it
        # instead of permanently dropping the comparison from calibration. A
        # terminal CANT_JUDGE (deterministic for a fixed packet) stays skipped.
        def _is_transient(v: dict) -> bool:
            return v.get("winner") == "CANT_JUDGE" and v.get("reason") in TRANSIENT_CANT_JUDGE

        already = {
            ev["verdict"]["comparison_id"]
            for ev in find_events(ledger_path, events.JUDGE_VERDICT)
            if not _is_transient(ev["verdict"])
        }
        # F-M-J3: the judge-scoped token ceiling (locked spec) — resume-aware:
        # prior verdicts' provider-reported usage seeds the accumulator, so a
        # re-run cannot reset the budget. Refuse-to-start, like the cost guard.
        ceiling = spec.judge.token_ceiling

        def _verdict_tokens(v: dict) -> int:
            u = (v.get("provenance") or {}).get("usage") or {}
            return int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)

        # Seed from BOTH native and reused verdicts so the shared judge budget is
        # resume-safe: reused-control judging counts against the same locked
        # ceiling and its prior spend is never forgotten on a re-run [F-M-J3].
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
        typer.echo(f"judged {judged} comparison(s)")
        if stopped_ceiling:
            typer.echo(
                f"stopped at the pre-registered judge token ceiling "
                f"({accumulated} >= {ceiling}); remaining comparisons refused "
                "[F-M-J3]", err=True,
            )

        # Control reuse [control-reuse plan]: also judge each fresh-contender vs
        # reused-control pair, recording reused_judge_verdict (a distinct kind the
        # official judge_preference / calibration never read). Exploratory-only,
        # but it draws on the SAME locked judge token budget — skip it entirely
        # once the ceiling has already stopped native judging, and thread the
        # running total through so reuse cannot spend past the cap [F-M-J3].
        if not stopped_ceiling:
            from .reuse import judge_reused

            n_reused = judge_reused(
                ledger_path, experiment_dir, spec, ctx,
                rubric=rubric, prompts=prompts, canaries=canaries,
                task_classes=task_classes, ceiling=ceiling, accumulated=accumulated,
            )
            if n_reused:
                typer.echo(f"judged {n_reused} reused-control comparison(s) [exploratory]")

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
                flag = (
                    " ESCALATE" if c.escalate
                    else (" INCONCLUSIVE" if c.inconclusive else "")
                )
                typer.echo(f"  class {cls}: n={c.n} kappa={c.kappa:.3f}{flag}")
