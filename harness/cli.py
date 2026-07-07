"""``bench`` CLI — verb registry; stories add subcommands [master plan §3.2].

``plan``, ``verify-chain``, ``anchor`` are thin shells over their stage APIs
(:mod:`harness.plan.api`, :mod:`harness.ledger.api`); each later story registers
its own verbs via ``register(app)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .cli_common import refusal_exit

app = typer.Typer(
    add_completion=False,
    help="verdi-bench — benchmark-grade A/B evaluation for agent stacks.",
    no_args_is_help=True,
)


@app.command()
def plan(
    experiment: Path = typer.Argument(..., help="Path to experiment.yaml"),
    ledger: Path = typer.Option(..., "--ledger", help="Ledger ndjson path"),
    acknowledge_underpowered: bool = typer.Option(
        False, "--acknowledge-underpowered", help="Lock despite an underpowered design"
    ),
    attested_by: Optional[str] = typer.Option(
        None, "--attested-by",
        help="Lock attester [D008]; defaults to the resolved --actor (PRA-L2)",
    ),
    corpus_manifest: Optional[Path] = typer.Option(
        None, "--corpus-manifest", help="Manifest whose calibration runs feed the power gate [PL-5]"
    ),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="Actor recorded on the lock event [GR-12]"
    ),
) -> None:
    """Validate, power-check, and write the genesis lock event."""
    from .plan.api import plan_experiment

    # refusal_exit() catches VerdiRefusal uniformly: every plan/lock refusal
    # (spec validation, actor, chain integrity, lock, rubric, task-commitment)
    # maps to a clean exit 2 — no verb-forgot-to-enumerate traceback, and the
    # structural pydantic vectors (extra key, single arm) no longer traceback
    # [refactor 13 OI-B].
    with refusal_exit():
        outcome = plan_experiment(
            experiment, ledger, acknowledge_underpowered=acknowledge_underpowered,
            attested_by=attested_by, corpus_manifest=corpus_manifest, actor=actor,
        )
    # The MDE scalar reads off the typed report; the ledgered flags (incl. the
    # lock-stage power_gate_skipped) live on the event payload the report rendered.
    mde = outcome.mde_report.mde
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
    from .ledger.api import verify_chain

    verdict = verify_chain(ledger, against_anchor=against_anchor)
    if not verdict.chain_ok:
        typer.echo(f"CHAIN BROKEN: {verdict.chain_detail}", err=True)
        raise typer.Exit(code=1)
    typer.echo("chain OK")
    if verdict.anchor_checked:
        if not verdict.anchor_ok:
            typer.echo(f"ANCHOR MISMATCH: {verdict.anchor_detail}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"anchor OK: {verdict.anchor_detail}")


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
    from .ledger.anchors import AnchorIntegrityError
    from .ledger.api import anchor as record_anchor

    try:
        with refusal_exit(ActorResolutionError):
            outcome = record_anchor(ledger, out=out, actor=actor)
    except AnchorIntegrityError as e:
        typer.echo(f"CHAIN BROKEN: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"anchored head={outcome.head_hash[:12]}… height={outcome.height}")


@app.command()
def init(
    directory: Path = typer.Argument(..., help="Target dir to scaffold (must be empty)"),
) -> None:
    """Scaffold experiment.yaml / tasks.yaml / rubric from the starter templates.

    The no-browser equivalent of the author surface's draft seeding [refactor 02
    §5]. NOT a ledgered operation (no entrypoint): it only writes files, then you
    edit and ``bench plan``. Refuses a non-empty target so it can never clobber
    work. Reads the shared template DATA files directly (the sdk-is-a-leaf
    contract forbids the CLI from importing the sdk package; the file is the
    shared contract, not the code).
    """
    import yaml

    templates = Path(__file__).resolve().parent / "sdk" / "templates"
    if directory.exists() and any(directory.iterdir()):
        typer.echo(f"{directory} is not empty — refusing to scaffold over it", err=True)
        raise typer.Exit(code=2)
    spec_text = (templates / "starter-experiment.yaml").read_text(encoding="utf-8")
    tasks_text = (templates / "starter-tasks.yaml").read_text(encoding="utf-8")
    rubric_text = (templates / "judge-rubric.md").read_text(encoding="utf-8")
    # The rubric lands where the spec's judge.rubric points, so the scaffold is
    # internally consistent (`bench plan` finds the rubric it commits).
    rubric_rel = (yaml.safe_load(spec_text).get("judge") or {}).get("rubric", "rubric.md")

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "experiment.yaml").write_text(spec_text, encoding="utf-8")
    (directory / "tasks.yaml").write_text(tasks_text, encoding="utf-8")
    rubric_path = directory / rubric_rel
    rubric_path.parent.mkdir(parents=True, exist_ok=True)
    rubric_path.write_text(rubric_text, encoding="utf-8")
    typer.echo(f"scaffolded {directory}: experiment.yaml, tasks.yaml, {rubric_rel}")
    typer.echo("  edit the files, then: bench plan experiment.yaml --ledger ledger.ndjson")


def _register_stage_commands() -> None:
    """Attach stage subcommands built in later stories, if present.

    Only a genuinely-absent stage module is tolerated: the except clause checks
    ``e.name`` against the module being imported, so a *transitive*
    ModuleNotFoundError (a missing dependency inside a present stage CLI — a
    real bug) propagates rather than degrading to a silently-missing
    subcommand [refactor 01 §4 D1].
    """
    from importlib import import_module

    for module_name, attr in [
        (".run.cli", "register"),
        (".hermetic.cli", "register"),
        (".images.cli", "register"),
        (".grade.cli", "register"),
        (".judge.cli", "register"),
        (".corpus.cli", "register"),
        (".analyze.cli", "register"),
        (".review.cli", "register"),
        (".process.cli", "register"),
        (".forensics.cli", "register"),
        (".contamination.cli", "register"),
        (".status.cli", "register"),
        (".serve.cli", "register"),
        (".author.cli", "register"),
    ]:
        try:
            mod = import_module(module_name, __package__)
        except ModuleNotFoundError as e:
            if e.name != f"{__package__}{module_name}":
                # A transitive miss inside a present stage CLI: re-raise —
                # swallowing it would silently drop the verb [refactor 01 §4 D1].
                raise
            continue  # pragma: no cover - the stage module itself is absent
        getattr(mod, attr)(app)


_register_stage_commands()


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
