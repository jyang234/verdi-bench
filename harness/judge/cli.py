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


def _judge_summary_line(outcome: JudgeOutcome) -> str:
    """The native ``bench judge`` count line [ux-friction AC-3].

    Terse when every comparison produced a substantive verdict (``judged N
    comparison(s)``); when any landed CANT_JUDGE, discloses the split with
    per-reason counts (e.g. a keyless real-provider judge → ``0 verdicts, 3
    cant_judge (provider_error ×3)``), so a fail-closed pass cannot read as N
    successes (F6). Same shape as grade's summary line; reasons sorted (no
    dict-ordering assumption). Pure, so the string is pinned without a CLI."""
    base = f"judged {outcome.judged} comparison(s)"
    if outcome.cant_judge == 0:
        return base
    reasons = ", ".join(
        f"{reason} ×{n}"
        for reason, n in sorted(outcome.cant_judge_reasons.items())
    )
    return (
        f"{base}: {outcome.verdicts} verdicts, {outcome.cant_judge} cant_judge "
        f"({reasons})"
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
            typer.echo(f"judged {outcome.n_reused} reused-control comparison(s) [exploratory]")

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
