"""``bench`` CLI — verb registry; stories add subcommands [master plan §3.2].

EVAL-3 ships ``plan``, ``verify-chain``, ``anchor``. EVAL-4 adds ``run``,
EVAL-5 adds ``grade`` (registered below as they are built).
"""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    add_completion=False,
    help="verdi-bench — benchmark-grade A/B evaluation for agent stacks.",
    no_args_is_help=True,
)


def _default_ctx(experiment_id: str):
    from .ledger.events import EventContext

    try:
        actor = getpass.getuser()
    except Exception:  # pragma: no cover - unusual environments
        actor = "unknown"
    return EventContext(experiment_id=experiment_id, actor=actor)


@app.command()
def plan(
    experiment: Path = typer.Argument(..., help="Path to experiment.yaml"),
    ledger: Path = typer.Option(..., "--ledger", help="Ledger ndjson path"),
    acknowledge_underpowered: bool = typer.Option(
        False, "--acknowledge-underpowered", help="Lock despite an underpowered design"
    ),
    attested_by: str = typer.Option("cli-user", "--attested-by", help="Lock attester [D008]"),
) -> None:
    """Validate, power-check, and write the genesis lock event."""
    from .plan.lock import UnderpoweredError, lock_experiment

    ctx = _default_ctx(experiment_id=experiment.stem)
    try:
        outcome = lock_experiment(
            experiment,
            ledger,
            ctx=ctx,
            acknowledge_underpowered=acknowledge_underpowered,
            attested_by=attested_by,
        )
    except UnderpoweredError as e:
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
    from .ledger.chain import verify_chain

    result = verify_chain(ledger)
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
) -> None:
    """Record the current chain head to an external anchor store [D008]."""
    from datetime import datetime, timezone

    from .ledger.anchors import anchor_head

    rec = anchor_head(ledger, out, ts=datetime.now(timezone.utc).isoformat())
    typer.echo(f"anchored head={rec['head_hash'][:12]}… height={rec['height']}")


def _register_stage_commands() -> None:
    """Attach stage subcommands built in later stories, if importable."""
    try:
        from .run.cli import register as register_run

        register_run(app)
    except Exception:  # pragma: no cover - stage not present yet
        pass
    try:
        from .grade.cli import register as register_grade

        register_grade(app)
    except Exception:  # pragma: no cover
        pass


_register_stage_commands()


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
