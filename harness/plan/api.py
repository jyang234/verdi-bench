"""``plan`` stage API [refactor 02 §3].

The importable entry point behind ``bench plan`` [EVAL-3]: resolve the actor,
feed the power gate real calibration variance when a corpus manifest is supplied,
commit the task content, and write the genesis lock event. This composes the
public ``lock_experiment`` seam (never its internals); the typer verb is a thin
shell that maps the refusals to exit codes and echoes the MDE + flags.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import VerdiRefusal


class ExperimentIdResolutionError(VerdiRefusal, RuntimeError):
    """The experiment path has no directory to name an ``experiment_id`` from
    [ux-friction AC-1].

    plan stamps ``provenance.experiment_id`` from the experiment *directory* —
    the resolved spec file's parent. A spec at the filesystem root leaves that
    name empty; rather than ledger ``experiment_id=''`` into the permanent
    hash-chained ledger (the F1 friction), refuse and name the offending path."""


def plan_experiment(
    experiment, ledger, *, acknowledge_underpowered: bool = False,
    attested_by=None, corpus_manifest=None, actor=None,
):
    """Validate, power-check, and write the genesis lock event; return the
    :class:`LockOutcome` [EVAL-3].

    Raises ``ExperimentIdResolutionError`` (an unnameable experiment directory),
    ``ActorResolutionError``, and the lock refusals (``UnderpoweredError``,
    ``AlreadyLockedError``, ``TaskCommitmentError``, ``ChainIntegrityError``,
    ``RubricCommitmentError``) the CLI maps to exit 2."""
    from ..corpus.commit import load_task_dicts
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from .lock import lock_experiment
    from .power import calibration_variance_from_runs

    experiment = Path(experiment)
    # PL-8 + [ux-friction AC-1]: stamp the experiment *directory* name, exactly as
    # run/grade do — one ledger, one experiment_id. Derive it from the RESOLVED
    # spec path so the cd-in form bench init itself prints
    # (`bench plan experiment.yaml`, whose unresolved `.parent` is `.` with an
    # empty name) stamps the real directory name instead of baking
    # experiment_id='' into every event of the permanent chain (F1). A resolved
    # parent with no name (a spec at the filesystem root) refuses rather than
    # ever ledgering an empty id.
    resolved = experiment.resolve()
    experiment_id = resolved.parent.name
    if not experiment_id:
        raise ExperimentIdResolutionError(
            f"cannot derive an experiment_id: the resolved experiment path "
            f"{resolved} has no parent directory to name (a spec at the "
            "filesystem root has no experiment directory). Place experiment.yaml "
            "inside a named experiment directory."
        )
    ctx = EventContext(experiment_id=experiment_id, actor=resolve_actor(actor))
    # PL-5: feed the power gate real calibration variance when a corpus manifest
    # with calibration runs is supplied; otherwise the lock falls back to
    # AssumedVariance (flagged assumption_based_mde).
    variance_source = None
    if corpus_manifest is not None:
        from ..corpus.registry import CorpusManifest

        manifest = CorpusManifest.load(corpus_manifest)
        variance_source = calibration_variance_from_runs(manifest.calibration.runs)
    # PL-7/D-6: commit the task content (tasks.yaml in the experiment dir) into the
    # lock so a post-lock swap is refused by run/grade.
    task_dicts = load_task_dicts(experiment.parent)
    return lock_experiment(
        experiment,
        ledger,
        ctx=ctx,
        acknowledge_underpowered=acknowledge_underpowered,
        attested_by=attested_by,
        task_dicts=task_dicts,
        variance_source=variance_source,
    )
