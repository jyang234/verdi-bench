"""``bench review …`` [EVAL-7 §M4, §M6].

``build`` renders the offline packet; ``record`` captures a verdict + the two
integrity questions strictly before any reveal; ``reveal`` unblinds only after a
verdict exists. The ordering is enforced by the tool (``reveal`` refuses early),
not by discipline.
"""

from __future__ import annotations

import getpass
from pathlib import Path

import typer


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover
        return "unknown"


def register(app: typer.Typer) -> None:
    review_app = typer.Typer(help="Human review packet + verdict capture [EVAL-7].",
                             no_args_is_help=True)

    @review_app.command("record")
    def review_record(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        comparison_id: str = typer.Option(..., "--comparison-id"),
        winner: str = typer.Option(..., "--winner", help="A | B | TIE | CANT_JUDGE"),
        reason: str = typer.Option("", "--reason"),
        arm_recognized: bool = typer.Option(
            False, "--arm-recognized", help="Could you identify the arm?"
        ),
        arm_guess: str = typer.Option(None, "--arm-guess", help="If recognized, your guess"),
    ) -> None:
        """Record a human verdict + integrity answers (strictly pre-reveal)."""
        from ..judge.schema import Evidence, Verdict, VerdictProvenance, Winner
        from ..ledger.events import EventContext
        from .record import ReviewError, record_human_verdict

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_actor())
        prov = VerdictProvenance(
            judge_model="human", rubric_sha256="human", packet_sha256="human",
            call_ids=["human"], orders="single", temperature=0.0, ts=ctx.clock(),
        )
        evidence = []
        if winner in ("A", "B"):
            evidence = [Evidence(kind="diff", response=winner, hunk="reviewer-cited")]
        verdict = Verdict(
            winner=Winner(winner), reason=reason or winner, evidence=evidence,
            provenance=prov, source="human", comparison_id=comparison_id,
        )
        try:
            record_human_verdict(
                ledger_path, ctx, verdict=verdict, arm_recognized=arm_recognized,
                arm_guess=arm_guess,
            )
        except ReviewError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        typer.echo(f"recorded human verdict for {comparison_id} (closes the comparison)")

    @review_app.command("reveal")
    def review_reveal(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        comparison_id: str = typer.Option(..., "--comparison-id"),
    ) -> None:
        """Unblind a comparison — refuses before a verdict exists."""
        from ..ledger.events import EventContext
        from .record import RevealError, reveal_comparison

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_actor())
        try:
            reveal_comparison(
                ledger_path, ctx, comparison_id=comparison_id,
                arm_identities={"1": "arm_a", "2": "arm_b"},
            )
        except RevealError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        typer.echo(f"revealed {comparison_id}")

    app.add_typer(review_app, name="review")
