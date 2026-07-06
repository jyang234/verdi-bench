"""``bench analyze`` / ``selfcheck`` / ``card`` [EVAL-6 §M6] — thin shells over
:mod:`harness.analyze.api`.

Argument parsing, actor resolution, refusal→exit mapping, echo; the findings
computation, selfcheck, and card rendering live in the stage API [refactor 02
§3]. ``run_analyze`` / ``run_selfcheck_cli`` are re-exported so the analyze tests
keep importing them from ``harness.analyze.cli``, and the one-event property
registrations fire here at import time [EVAL-3 §M7, XC-3].
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..cli_common import refusal_exit, resolve_actor_or_exit
from .api import (  # noqa: F401 — run_analyze/run_selfcheck_cli re-exported for tests
    compare_card_files,
    emit_card,
    run_analyze,
    run_selfcheck_cli,
)
from .card import CardError


def register(app: typer.Typer) -> None:
    @app.command()
    def selfcheck(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the selfcheck event [GR-12]"),
    ) -> None:
        """Run the D008 coverage selfcheck; official render requires it to pass."""
        resolved_actor = resolve_actor_or_exit(actor)
        result = run_selfcheck_cli(experiment_dir, actor=resolved_actor)
        status = "PASS" if result["passed"] else "FAIL"
        typer.echo(
            f"selfcheck {status}: method={result['selected_method']} "
            f"coverage={result['coverage']} nominal={result['nominal']} "
            f"mc_interval={result['mc_interval']} null_model={result['null_model']}"
        )
        if not result["passed"]:
            typer.echo(
                "the experiment is exploratory-only until the selfcheck passes "
                "(official render refused)", err=True,
            )

    @app.command()
    def analyze(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        official: bool = typer.Option(False, "--official", help="Official render (fenced)"),
        exploratory: bool = typer.Option(
            False, "--exploratory", help="Exploratory render (watermarked)"
        ),
        corpus: Path = typer.Option(
            None, "--corpus", help="Corpus manifest.json for provenance + calibration gate"
        ),
        html: bool = typer.Option(False, "--html", help="Render HTML instead of markdown"),
        actor: str = typer.Option(
            None, "--actor", help="Actor recorded on the findings event [GR-12]"
        ),
    ) -> None:
        """Render pre-registered official or exploratory findings."""
        if official and exploratory:
            raise typer.BadParameter("choose at most one of --official/--exploratory")
        mode = "official" if official else "exploratory"
        resolved_actor = resolve_actor_or_exit(actor)
        out = run_analyze(experiment_dir, mode=mode, corpus=corpus, html=html,
                          actor=resolved_actor)
        if out is None:
            typer.echo(f"refused {mode} render; recorded cant_analyze", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"rendered {mode} findings → {out}")

    card_app = typer.Typer(
        help="Result cards: comparable, citable run summaries [read-only].",
        no_args_is_help=True,
    )

    @card_app.command("emit")
    def card_emit(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        corpus: Path = typer.Option(
            None, "--corpus",
            help="Corpus manifest.json → image-insensitive, cross-mirror battery_sha",
        ),
        fmt: str = typer.Option(
            "json", "--format",
            help="json (canonical/comparable) | md (human) | html (self-contained)",
        ),
        out: Path = typer.Option(None, "--out", help="Write the card here (default: stdout)"),
    ) -> None:
        """Emit a benchmark result card — a read-only projection of an analyzed run.

        Requires a prior `bench analyze` (the card certifies a rendered result).
        The `json` form is the canonical, comparable artifact (feed it to
        `card compare`); `md`/`html` are human renders of the same card.
        """
        if fmt not in ("json", "md", "html"):
            raise typer.BadParameter("--format must be json, md, or html")
        with refusal_exit(CardError):
            outcome = emit_card(experiment_dir, corpus=corpus, fmt=fmt, out=out)
        if out is not None:
            typer.echo(
                f"card ({fmt}) → {out} "
                f"(battery_sha={outcome.battery_sha[:12]}…, basis={outcome.battery_basis})"
            )
        else:
            typer.echo(outcome.text)

    @card_app.command("compare")
    def card_compare(
        card_a: Path = typer.Argument(..., help="First card json"),
        card_b: Path = typer.Argument(..., help="Second card json"),
    ) -> None:
        """Compare two cards; refuse loudly if they graded different task sets/metrics."""
        import json as _json

        with refusal_exit(CardError):
            result = compare_card_files(card_a, card_b)
        typer.echo(_json.dumps(result, indent=2, sort_keys=True))

    app.add_typer(card_app, name="card")


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
def _prepare_analyze(ctx_dir: str) -> None:
    # lock the experiment so the render has a spec to analyze (one event, seeded
    # before the sweep snapshots the count).
    from ..ledger.events import EventContext
    from ..plan.lock import lock_experiment

    d = Path(ctx_dir)
    lock_experiment(
        d / "experiment.yaml", d / "ledger.ndjson",
        ctx=EventContext(experiment_id="prop"), n_sim=8, n_boot=40, deltas=[0.2, 0.4],
    )


def _analyze_entrypoint(ctx_dir: str) -> None:
    # An official render with no calibrated corpus fails closed to exactly one
    # cant_analyze event (the AN-3 path).
    run_analyze(Path(ctx_dir), mode="official", corpus=None, actor="prop")


def _selfcheck_entrypoint(ctx_dir: str) -> None:
    # With no trials the selfcheck is insufficient_data (n<2 clusters) and lands
    # exactly one additive `selfcheck` event — the one-event property [7I-1].
    run_selfcheck_cli(Path(ctx_dir), actor="prop", n_sim=8, n_boot=40)


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("analyze", _analyze_entrypoint, prepare=_prepare_analyze)
    register_entrypoint("selfcheck", _selfcheck_entrypoint, prepare=_prepare_analyze)


_register()
