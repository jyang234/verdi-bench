"""``bench process …`` [EVAL-9 §M3].

``record`` captures a **human** process score and is reachable only after the
comparison's EVAL-7 reveal (the CLI refuses earlier). ``score`` runs the isolated
**judge** process-scoring path over a post-redaction transcript.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path

import typer


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover
        return "unknown"


def register(app: typer.Typer) -> None:
    process_app = typer.Typer(help="Transcript process rubric scoring [EVAL-9].",
                              no_args_is_help=True)

    @process_app.command("record")
    def process_record(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        trial_id: str = typer.Option(..., "--trial-id"),
        comparison_id: str = typer.Option(..., "--comparison-id"),
        scores_json: Path = typer.Option(..., "--scores", help="JSON {dim_id: 1-5 | 'CANT_SCORE'}"),
        rubric_path: Path = typer.Option(None, "--rubric", help="Rubric YAML (default: v1)"),
    ) -> None:
        """Record a human process score — refused before the EVAL-7 reveal."""
        from ..ledger.events import EventContext
        from .rubric import ProcessRubric, default_rubric
        from .score import (
            ProcessSequencingError,
            human_scores_from_mapping,
            record_human_process_score,
        )

        rubric = ProcessRubric.from_yaml(rubric_path) if rubric_path else default_rubric()
        raw = json.loads(scores_json.read_text(encoding="utf-8"))
        # PR-7: a typoed/unknown or missing dimension is a loud error, not a silent
        # CANT_SCORE("human_cant") that degrades a real score.
        try:
            dimension_scores = human_scores_from_mapping(raw, rubric)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_actor())
        try:
            record_human_process_score(
                trial_id, rubric, dimension_scores, ledger_path=ledger_path, ctx=ctx,
                ts=ctx.clock(), scorer_id=_actor(), comparison_id=comparison_id,
            )
        except ProcessSequencingError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        typer.echo(f"recorded human process score for {trial_id}")

    app.add_typer(process_app, name="process")
