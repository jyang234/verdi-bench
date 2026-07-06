"""``process`` stage API [refactor 02 §3].

The importable entry points behind ``bench process score|record`` [EVAL-9 §M3]:
``process_score`` runs the isolated **judge** process-scoring path over each
post-redaction transcript; ``process_record`` captures a **human** process score
(reachable only after the EVAL-7 reveal, which ``record_human_process_score``
enforces). The typer verbs are thin shells that map the refusals to exit codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessScoreOutcome:
    """What ``bench process score`` computed: the number of trials scored."""

    scored: int


def process_score(exp_dir, *, rubric_path=None, actor=None) -> ProcessScoreOutcome:
    """Judge-score every unscored trial's process from its transcript [AC-4].

    Raises ``TaskCommitmentError`` / ``ActorResolutionError`` (the CLI maps to
    exit 2); a malformed ``--rubric`` propagates (a bad rubric is a loud error,
    exactly as the inline body left it)."""
    from ..corpus.commit import assert_task_commitment, load_task_dicts
    from ..judge.assemble import comparison_id_for
    from ..ledger import events
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from ..ledger.query import find_events
    from ..plan.lock import assert_lock
    from ..run.artifacts import read_transcript
    from .rubric import ProcessRubric, default_rubric
    from .score import TRANSIENT_CANT_SCORE, score_trial_process

    exp_dir = Path(exp_dir)
    spec_path = exp_dir / "experiment.yaml"
    ledger_path = exp_dir / "ledger.ndjson"
    _lock = assert_lock(spec_path, ledger_path)
    lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read
    task_dicts = load_task_dicts(exp_dir)
    assert_task_commitment(
        lock_event, task_dicts,
        corpus_id=spec.corpus.id, semver=spec.corpus.version,
    )

    rubric = ProcessRubric.from_yaml(rubric_path) if rubric_path else default_rubric()
    resolved_actor = resolve_actor(actor)
    ctx = EventContext(experiment_id=exp_dir.name, actor=resolved_actor)

    # PRA-M13: a process score whose every dimension failed *transiently* (the
    # scorer could not run — timeout / provider_error) is not counted as done, so
    # a re-run re-attempts it. A score with any real dimension, or a terminal cant
    # reason, stays skipped.
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
        transcript = read_transcript(rec.get("artifacts_path"))
        score_trial_process(
            trial_id, transcript, rubric, ledger_path=ledger_path, ctx=ctx,
            ts=ctx.clock(), scorer_id=spec.judge.model, spec=spec,
            provider_model=spec.judge.model,
            comparison_id=comparison_id_for(rec["task_id"], rec["repetition"]),
        )
        n += 1
    return ProcessScoreOutcome(scored=n)


def process_record(
    exp_dir, *, trial_id: str, comparison_id: str, scores: dict, rubric, actor=None
) -> None:
    """Record a human process score — refused before the EVAL-7 reveal.

    ``scores`` is a ``{dim_id: 1..5 | 'CANT_SCORE'}`` mapping and ``rubric`` the
    already-loaded rubric (a malformed ``--rubric`` is the CLI's loud error, kept
    outside this envelope). Raises ``ValueError`` (bad/unknown dimension),
    ``ActorResolutionError``, and ``ProcessSequencingError`` (pre-reveal) — all
    mapped to exit 2 by the CLI."""
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from .score import human_scores_from_mapping, record_human_process_score

    # PR-7: a typoed/unknown or missing dimension is a loud error, not a silent
    # CANT_SCORE("human_cant") that degrades a real score.
    dimension_scores = human_scores_from_mapping(scores, rubric)
    who = resolve_actor(actor)
    ledger_path = Path(exp_dir) / "ledger.ndjson"
    ctx = EventContext(experiment_id=Path(exp_dir).name, actor=who)
    record_human_process_score(
        trial_id, rubric, dimension_scores, ledger_path=ledger_path, ctx=ctx,
        ts=ctx.clock(), scorer_id=who, comparison_id=comparison_id,
    )
