"""``bench process …`` [EVAL-9 §M3] — thin shell over :mod:`harness.process.api`.

``record`` captures a **human** process score and is reachable only after the
comparison's EVAL-7 reveal (the API refuses earlier). ``score`` runs the isolated
**judge** process-scoring path over a post-redaction transcript. The verbs map
the enumerated refusals to exit codes and echo the count.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..cli_common import refusal_exit
from ..corpus.commit import TaskCommitmentError
from ..ledger.actor import ActorResolutionError
from .api import process_record, process_score
from .rubric import ProcessRubric, default_rubric
from .score import ProcessSequencingError


def register(app: typer.Typer) -> None:
    process_app = typer.Typer(help="Transcript process rubric scoring [EVAL-9].",
                              no_args_is_help=True)

    @process_app.command("score")
    def score_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml"),
        rubric_path: Path = typer.Option(None, "--rubric", help="Rubric YAML (default: v1)"),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the score events [GR-12]"),
    ) -> None:
        """Judge-score every unscored trial's process from its transcript [AC-4]."""
        with refusal_exit(TaskCommitmentError, ActorResolutionError):
            outcome = process_score(experiment_dir, rubric_path=rubric_path, actor=actor)
        typer.echo(f"process-scored {outcome.scored} trial(s)")

    @process_app.command("record")
    def record_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        trial_id: str = typer.Option(..., "--trial-id"),
        comparison_id: str = typer.Option(..., "--comparison-id"),
        scores_json: Path = typer.Option(..., "--scores", help="JSON {dim_id: 1-5 | 'CANT_SCORE'}"),
        rubric_path: Path = typer.Option(None, "--rubric", help="Rubric YAML (default: v1)"),
        actor: str = typer.Option(None, "--actor", help="Human scorer identity [GR-12]"),
    ) -> None:
        """Record a human process score — refused before the EVAL-7 reveal."""
        # A malformed --rubric is a loud error (traceback), kept outside the
        # refusal envelope so it is never confused with a bad --scores mapping.
        rubric = ProcessRubric.from_yaml(rubric_path) if rubric_path else default_rubric()
        raw = json.loads(scores_json.read_text(encoding="utf-8"))
        with refusal_exit(ValueError, ActorResolutionError, ProcessSequencingError):
            process_record(
                experiment_dir, trial_id=trial_id, comparison_id=comparison_id,
                scores=raw, rubric=rubric, actor=actor,
            )
        typer.echo(f"recorded human process score for {trial_id}")

    app.add_typer(process_app, name="process")
