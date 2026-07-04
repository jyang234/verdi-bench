"""``bench`` CLI — verb registry; stories add subcommands [master plan §3.2].

EVAL-3 ships ``plan``, ``verify-chain``, ``anchor``. EVAL-4 adds ``run``,
EVAL-5 adds ``grade`` (registered below as they are built).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    add_completion=False,
    help="verdi-bench — benchmark-grade A/B evaluation for agent stacks.",
    no_args_is_help=True,
)


def _default_ctx(experiment_id: str, actor_flag: Optional[str] = None):
    from .ledger.actor import resolve_actor
    from .ledger.events import EventContext

    return EventContext(experiment_id=experiment_id, actor=resolve_actor(actor_flag))


@app.command()
def plan(
    experiment: Path = typer.Argument(..., help="Path to experiment.yaml"),
    ledger: Path = typer.Option(..., "--ledger", help="Ledger ndjson path"),
    acknowledge_underpowered: bool = typer.Option(
        False, "--acknowledge-underpowered", help="Lock despite an underpowered design"
    ),
    attested_by: str = typer.Option("cli-user", "--attested-by", help="Lock attester [D008]"),
    corpus_manifest: Optional[Path] = typer.Option(
        None, "--corpus-manifest", help="Manifest whose calibration runs feed the power gate [PL-5]"
    ),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="Actor recorded on the lock event [GR-12]"
    ),
) -> None:
    """Validate, power-check, and write the genesis lock event."""
    from .corpus.commit import TaskCommitmentError, load_task_dicts
    from .ledger.actor import ActorResolutionError
    from .ledger.query import ChainIntegrityError
    from .plan.lock import (
        AlreadyLockedError,
        RubricCommitmentError,
        UnderpoweredError,
        lock_experiment,
    )
    from .plan.power import calibration_variance_from_runs

    # PL-8: stamp the experiment *directory* name, exactly as run/grade do
    # (``experiment_dir.name``) — one ledger, one experiment_id. No stem fallback:
    # that diverged from run/grade for a bare path.
    try:
        ctx = _default_ctx(experiment_id=experiment.parent.name, actor_flag=actor)
    except ActorResolutionError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)
    # PL-5: feed the power gate real calibration variance when a corpus manifest
    # with calibration runs is supplied; otherwise the lock falls back to
    # AssumedVariance (flagged assumption_based_mde).
    variance_source = None
    if corpus_manifest is not None:
        from .corpus.registry import CorpusManifest

        manifest = CorpusManifest.load(corpus_manifest)
        variance_source = calibration_variance_from_runs(manifest.calibration.runs)
    try:
        # PL-7/D-6: commit the task content (tasks.yaml in the experiment dir) into
        # the lock so a post-lock swap is refused by run/grade.
        task_dicts = load_task_dicts(experiment.parent)
        outcome = lock_experiment(
            experiment,
            ledger,
            ctx=ctx,
            acknowledge_underpowered=acknowledge_underpowered,
            attested_by=attested_by,
            task_dicts=task_dicts,
            variance_source=variance_source,
        )
    except (
        UnderpoweredError,
        AlreadyLockedError,
        TaskCommitmentError,
        ChainIntegrityError,
        RubricCommitmentError,
    ) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)
    mde = outcome.mde["mde"]
    flags = ", ".join(outcome.mde["flags"]) or "none"
    typer.echo(f"locked {experiment} (sha256={outcome.spec_sha256[:12]}…)")
    typer.echo(f"  MDE={mde}  flags={flags}")


@app.command("verify-chain")
def verify_chain_cmd(
    ledger: Path = typer.Argument(..., help="Ledger ndjson path"),
    against_anchor: Optional[Path] = typer.Option(
        None, "--against-anchor", help="Cross-check anchored history"
    ),
) -> None:
    """Verify the hash chain; nonzero exit names the first broken link."""
    from .ledger.anchors import verify_against_anchor
    from .ledger.query import verify  # read-side seam; never import ledger.chain directly

    result = verify(ledger)
    if not result.ok:
        typer.echo(f"CHAIN BROKEN: {result.detail}", err=True)
        raise typer.Exit(code=1)
    typer.echo("chain OK")
    if against_anchor is not None:
        ar = verify_against_anchor(ledger, against_anchor)
        if not ar.ok:
            typer.echo(f"ANCHOR MISMATCH: {ar.detail}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"anchor OK: {ar.detail}")


@app.command()
def anchor(
    ledger: Path = typer.Argument(..., help="Ledger ndjson path"),
    out: Path = typer.Option(..., "--out", help="External anchor store path"),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="Actor recorded on the chain_anchor event [GR-12]"
    ),
) -> None:
    """Record the current chain head to an external anchor store [D008].

    Also ledgers a ``chain_anchor`` event so the act of anchoring is itself an
    auditable, chained record — not just an external-file side effect [PL-4].
    """
    from .ledger.actor import ActorResolutionError
    from .ledger.anchors import AnchorIntegrityError, anchor_head
    from .ledger.events import record_chain_anchor

    # Route the timestamp through the EventContext clock seam rather than a bare
    # wall-clock read in the CLI [PL-4 / determinism]. Capture the pre-anchor
    # head externally, then ledger that same head.
    try:
        ctx = _default_ctx(experiment_id=ledger.parent.name, actor_flag=actor)
    except ActorResolutionError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)
    # 7A-2: anchor_head chain-verifies first and refuses tampered history before
    # writing anything; exit 1 and append neither the anchor line nor the event.
    try:
        rec = anchor_head(ledger, out, ts=ctx.clock())
    except AnchorIntegrityError as e:
        typer.echo(f"CHAIN BROKEN: {e}", err=True)
        raise typer.Exit(code=1)
    record_chain_anchor(ledger, ctx, head_hash=rec["head_hash"], height=rec["height"])
    typer.echo(f"anchored head={rec['head_hash'][:12]}… height={rec['height']}")


def _register_stage_commands() -> None:
    """Attach stage subcommands built in later stories, if present.

    Only a genuinely-absent module is tolerated (ModuleNotFoundError); any other
    error (a real bug inside a present stage CLI) propagates rather than
    degrading to a silently-missing subcommand.
    """
    from importlib import import_module

    for module_name, attr in [
        (".run.cli", "register"),
        (".grade.cli", "register"),
        (".judge.cli", "register"),
        (".corpus.cli", "register"),
        (".analyze.cli", "register"),
        (".review.cli", "register"),
        (".process.cli", "register"),
        (".contamination.cli", "register"),
    ]:
        try:
            mod = import_module(module_name, __package__)
        except ModuleNotFoundError:  # pragma: no cover - stage not present yet
            continue
        getattr(mod, attr)(app)


_register_stage_commands()


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
