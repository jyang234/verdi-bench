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
from .api import JudgeRubricError, judge_experiment


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
        typer.echo(f"judged {outcome.judged} comparison(s)")
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
