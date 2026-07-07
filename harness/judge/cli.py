"""``bench judge`` [EVAL-2 §M5, JD-9] — thin shell over :mod:`harness.judge.api`.

Parses the flags, maps the enumerated refusals to exit 2, and renders the
counts + per-class kappa summary; the judging logic lives in the stage API
[refactor 02 §3].
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..cli_common import refusal_exit
from ..corpus.commit import TaskCommitmentError
from ..ledger.actor import ActorResolutionError
from .api import JudgeOutcome, JudgeRubricError, judge_experiment


def _cant_judge_clause(
    base: str, *, verdicts: int, cant_judge: int, cant_judge_reasons: dict, suffix: str = "",
) -> str:
    """Shared body of the ``bench judge`` summary lines [ux-friction AC-3].

    Terse ``{base}{suffix}`` when no comparison landed CANT_JUDGE; when any did,
    discloses the split with per-reason counts — ``{base}: X verdicts, Y cant_judge
    (reason ×n){suffix}`` — so a fail-closed pass cannot read as N successes (F6).
    Same shape as grade's summary line; reasons sorted (no dict-ordering
    assumption). ``suffix`` (the reused line's `` [exploratory]``) stays LAST in both
    branches. Pure, so each line is pinned without a CLI."""
    if cant_judge == 0:
        return f"{base}{suffix}"
    reasons = ", ".join(
        f"{reason} ×{n}" for reason, n in sorted(cant_judge_reasons.items())
    )
    return f"{base}: {verdicts} verdicts, {cant_judge} cant_judge ({reasons}){suffix}"


def _judge_summary_line(outcome: JudgeOutcome) -> str:
    """The native ``bench judge`` count line [ux-friction AC-3]: ``judged N
    comparison(s)``, disclosing ``N: X verdicts, Y cant_judge (reason ×n)`` when any
    comparison landed CANT_JUDGE (a keyless real-provider judge → provider_error)."""
    return _cant_judge_clause(
        f"judged {outcome.judged} comparison(s)",
        verdicts=outcome.verdicts, cant_judge=outcome.cant_judge,
        cant_judge_reasons=outcome.cant_judge_reasons,
    )


def _reused_judge_summary_line(outcome: JudgeOutcome) -> str:
    """The exploratory reused-control count line [ux-friction AC-3 residual].

    The reused pass runs the same JudgingSession as the native pass, so it too lands
    CANT_JUDGE (a keyless real-provider judge → provider_error) — which the bare
    ``judged N reused-control comparison(s) [exploratory]`` hid as N successes. Same
    disclosure as the native line, with the ``[exploratory]`` marker kept LAST. It
    names THIS pass's outcomes: reuse retries a transient cant_judge on a later pass
    (the shared session skips only NON-transient verdicts), so a re-run can turn a
    provider_error into a verdict — the line is the current pass, not a cumulative
    tally. Pure, so the string is pinned without a CLI."""
    return _cant_judge_clause(
        f"judged {outcome.n_reused} reused-control comparison(s)",
        verdicts=outcome.reused_verdicts, cant_judge=outcome.reused_cant_judge,
        cant_judge_reasons=outcome.reused_cant_judge_reasons, suffix=" [exploratory]",
    )


def register(app: typer.Typer) -> None:
    @app.command()
    def judge(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        actor: str = typer.Option(
            None, "--actor", help="Actor recorded on the verdict events [GR-12]"
        ),
    ) -> None:
        """Judge every graded comparison; append one verdict each."""
        with refusal_exit(TaskCommitmentError, JudgeRubricError, ActorResolutionError):
            outcome = judge_experiment(experiment_dir, actor=actor)

        if outcome.rubric_warning:
            typer.echo(
                "WARNING: lock predates rubric commitment (D-P7-6); the rubric "
                "content is not pinned for this experiment", err=True,
            )
        typer.echo(_judge_summary_line(outcome))
        if outcome.stopped_ceiling:
            typer.echo(
                f"stopped at the pre-registered judge token ceiling "
                f"({outcome.accumulated} >= {outcome.ceiling}); remaining comparisons refused "
                "[F-M-J3]", err=True,
            )
        if outcome.n_reused:
            typer.echo(_reused_judge_summary_line(outcome))

        for cls in sorted(outcome.calibration):
            c = outcome.calibration[cls]
            if not c.sufficient:
                typer.echo(f"  class {cls}: n={c.n} (insufficient for kappa)")
            else:
                flag = (
                    " ESCALATE" if c.escalate
                    else (" INCONCLUSIVE" if c.inconclusive else "")
                )
                typer.echo(f"  class {cls}: n={c.n} kappa={c.kappa:.3f}{flag}")
