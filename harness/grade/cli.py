"""``bench grade`` [EVAL-5 §M5] — thin shell over :mod:`harness.grade.api`.

Parses the flags, maps the enumerated refusals to exit codes, and echoes the
graded count; the grading logic lives in the stage API [refactor 02 §3]. The
underscore helpers are re-exported so the white-box grade tests keep importing
them from ``harness.grade.cli``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..cli_common import refusal_exit
from ..corpus.commit import TaskCommitmentError
from ..ledger.actor import ActorResolutionError
from .api import (  # noqa: F401 — re-exported for the white-box grade tests
    GradeOutcome,
    GraderUnavailableRefusal,
    RetryTerminalError,
    _completed_trials,
    _grade_tasks_from_dicts,
    _resolve_terminal_overrides,
    grade_experiment,
)


def register(app: typer.Typer) -> None:
    @app.command()
    def grade(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        runner: str = typer.Option(
            "docker", "--runner", help="docker (real container) | local (no-daemon fake/test)"
        ),
        retry_terminal: list[str] = typer.Option(
            [], "--retry-terminal",
            help="Trial id with a terminal cant_grade to re-attempt (repeatable); "
                 "the resulting event records override_of [D-P7-2]",
        ),
        actor: str = typer.Option(
            None, "--actor", help="Actor recorded on the grade events [GR-12]"
        ),
    ) -> None:
        """Grade every ungraded trial deterministically."""
        # F-M-I3: a typo'd runner must refuse, never silently select docker —
        # validated before any I/O, like analyze's flag validation.
        if runner not in ("docker", "local"):
            raise typer.BadParameter("--runner must be docker or local")
        # A down grader marks pending trials transient and refuses (exit 1); the
        # pre-registration refusals map to exit 2 [7B-1/GR-8, PL-7/D-6, D-P7-2].
        with refusal_exit(GraderUnavailableRefusal, code=1):
            with refusal_exit(TaskCommitmentError, RetryTerminalError, ActorResolutionError):
                outcome = grade_experiment(
                    experiment_dir, runner=runner,
                    retry_terminal=retry_terminal, actor=actor,
                )
        typer.echo(f"graded {outcome.graded} trial(s)")
