"""``bench process …`` [EVAL-9 §M3].

``record`` captures a **human** process score and is reachable only after the
comparison's EVAL-7 reveal (the CLI refuses earlier). ``score`` runs the isolated
**judge** process-scoring path over a post-redaction transcript.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..ledger.actor import ActorResolutionError, resolve_actor


def _resolve_actor_or_exit(flag_value):
    """Resolve the ledgered actor or exit 2 with the named refusal [GR-12]."""
    try:
        return resolve_actor(flag_value)
    except ActorResolutionError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)


def _read_transcript(artifacts_path) -> str:
    """The trial's post-redaction transcript (``artifacts/transcript.txt``), or an
    empty string if absent — an empty transcript scores fail-closed, never a
    fabricated one."""
    if not artifacts_path:
        return ""
    p = Path(artifacts_path) / "transcript.txt"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def register(app: typer.Typer) -> None:
    process_app = typer.Typer(help="Transcript process rubric scoring [EVAL-9].",
                              no_args_is_help=True)

    @process_app.command("score")
    def process_score(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml"),
        rubric_path: Path = typer.Option(None, "--rubric", help="Rubric YAML (default: v1)"),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the score events [GR-12]"),
    ) -> None:
        """Judge-score every unscored trial's process from its transcript [AC-4]."""
        from ..corpus.commit import (
            TaskCommitmentError,
            assert_task_commitment,
            load_task_dicts,
        )
        from ..judge.assemble import comparison_id_for
        from ..ledger import events
        from ..ledger.events import EventContext
        from ..ledger.query import find_events
        from ..plan.lock import assert_lock
        from .rubric import ProcessRubric, default_rubric
        from .score import score_trial_process

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        _lock = assert_lock(spec_path, ledger_path)
        lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read
        task_dicts = load_task_dicts(experiment_dir)
        try:
            assert_task_commitment(
                lock_event, task_dicts,
                corpus_id=spec.corpus.id, semver=spec.corpus.version,
            )
        except TaskCommitmentError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)

        rubric = ProcessRubric.from_yaml(rubric_path) if rubric_path else default_rubric()
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_resolve_actor_or_exit(actor))
        # PRA-M13: a process score whose every dimension failed *transiently*
        # (the scorer could not run — timeout / provider_error) is not counted as
        # done, so a re-run re-attempts it. A score with any real dimension, or a
        # terminal cant reason, stays skipped (re-running would only duplicate the
        # good dimensions or reproduce the deterministic failure).
        from .score import TRANSIENT_CANT_SCORE

        def _fully_transient(ps: dict) -> bool:
            scores = ps.get("scores") or []
            return bool(scores) and all(
                d.get("score") is None
                and d.get("cant_score_reason") in TRANSIENT_CANT_SCORE
                for d in scores
            )

        already = {
            ev["process_score"]["trial_id"]
            for ev in find_events(ledger_path, events.PROCESS_SCORE)
            if not _fully_transient(ev["process_score"])
        }

        n = 0
        for ev in find_events(ledger_path, events.TRIAL):
            rec = ev["trial_record"]
            trial_id = rec["trial_id"]
            if trial_id in already:
                continue
            transcript = _read_transcript(rec.get("artifacts_path"))
            score_trial_process(
                trial_id, transcript, rubric, ledger_path=ledger_path, ctx=ctx,
                ts=ctx.clock(), scorer_id=spec.judge.model, spec=spec,
                provider_model=spec.judge.model,
                comparison_id=comparison_id_for(rec["task_id"], rec["repetition"]),
            )
            n += 1
        typer.echo(f"process-scored {n} trial(s)")

    @process_app.command("record")
    def process_record(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        trial_id: str = typer.Option(..., "--trial-id"),
        comparison_id: str = typer.Option(..., "--comparison-id"),
        scores_json: Path = typer.Option(..., "--scores", help="JSON {dim_id: 1-5 | 'CANT_SCORE'}"),
        rubric_path: Path = typer.Option(None, "--rubric", help="Rubric YAML (default: v1)"),
        actor: str = typer.Option(None, "--actor", help="Human scorer identity [GR-12]"),
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

        who = _resolve_actor_or_exit(actor)
        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=who)
        try:
            record_human_process_score(
                trial_id, rubric, dimension_scores, ledger_path=ledger_path, ctx=ctx,
                ts=ctx.clock(), scorer_id=who, comparison_id=comparison_id,
            )
        except ProcessSequencingError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        typer.echo(f"recorded human process score for {trial_id}")

    app.add_typer(process_app, name="process")
