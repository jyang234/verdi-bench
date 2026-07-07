"""``bench review …`` [EVAL-7 §M4, §M6] — thin shells over :mod:`harness.review.api`.

``build`` renders the offline packet; ``record`` captures a verdict + the two
integrity questions strictly before any reveal; ``reveal`` unblinds only after a
verdict exists. The ordering is enforced by the tool (``reveal`` refuses early),
not by discipline. ``serve`` stays a server entrypoint here (its own surface —
never the operator view).
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..cli_common import refusal_exit, resolve_actor_or_exit
from ..corpus.commit import TaskCommitmentError
from ..ledger.actor import ActorResolutionError
from .api import review_build, review_record, review_reveal
from .record import ReviewError, RevealError


def register(app: typer.Typer) -> None:
    review_app = typer.Typer(help="Human review packet + verdict capture [EVAL-7].",
                             no_args_is_help=True)

    @review_app.command("build")
    def build_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml"),
        out: Path = typer.Option(None, "--out", help="Packet HTML path [default: <dir>/review_packet.html]"),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the packet events [GR-12]"),
    ) -> None:
        """Sample + render the blinded review packet; record the Response↔arm map."""
        with refusal_exit(TaskCommitmentError, ActorResolutionError):
            outcome = review_build(experiment_dir, out=out, actor=actor)
        typer.echo(
            f"built review packet: {outcome.n_comparisons} comparison(s) "
            f"-> {outcome.out_path}"
        )

    @review_app.command("record")
    def record_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml"),
        comparison_id: str = typer.Option(..., "--comparison-id"),
        winner: str = typer.Option(
            ..., "--winner", help="the winning response: 1 | 2 | TIE | CANT_JUDGE"
        ),
        reason: str = typer.Option("", "--reason"),
        arm_recognized: bool = typer.Option(
            False, "--arm-recognized", help="Could you identify the arm?"
        ),
        arm_guess: str = typer.Option(
            None, "--arm-guess", help="If recognized, your guess of Response 1's arm"
        ),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the verdict [GR-12]"),
    ) -> None:
        """Record a human verdict + integrity answers (strictly pre-reveal).

        The human picks a **response** (1/2) as shown in the packet; the recorded
        winner is translated to the judge's A/B (arm) frame via the comparison's
        ``review_packet_built`` map, so the kappa join is frame-correct (RV-6/RV-9).
        """
        with refusal_exit(ActorResolutionError, ReviewError):
            review_record(
                experiment_dir, comparison_id=comparison_id, winner=winner,
                reason=reason, arm_recognized=arm_recognized, arm_guess=arm_guess,
                actor=actor,
            )
        typer.echo(f"recorded human verdict for {comparison_id} (closes the comparison)")

    @review_app.command("reveal")
    def reveal_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        comparison_id: str = typer.Option(..., "--comparison-id"),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the reveal [GR-12]"),
    ) -> None:
        """Unblind a comparison — refuses before a verdict exists."""
        with refusal_exit(ActorResolutionError, RevealError):
            identities = review_reveal(experiment_dir, comparison_id=comparison_id, actor=actor)
        typer.echo(f"revealed {comparison_id}: {identities}")

    @review_app.command("serve")
    def review_serve(
        experiment_dir: Path = typer.Argument(..., help="Dir with the built review packet"),
        reviewer: str = typer.Option(
            None, "--reviewer", help="The reviewer recorded on every verdict/reveal [GR-12]"
        ),
        host: str = typer.Option(
            None, "--host", help="Bind address (default 127.0.0.1 — loopback only)"
        ),
        port: int = typer.Option(
            None, "--port", help="Port (default 8395; 0 = OS-assigned)"
        ),
    ) -> None:
        """Blinded capture-then-reveal queue (its own surface — never the operator view)."""
        from .serve import DEFAULT_HOST, DEFAULT_REVIEW_PORT, make_review_server

        resolved = resolve_actor_or_exit(reviewer)
        srv = make_review_server(
            Path(experiment_dir),
            reviewer=resolved,
            host=host if host is not None else DEFAULT_HOST,
            port=port if port is not None else DEFAULT_REVIEW_PORT,
        )
        bound_host, bound_port = srv.server_address[:2]
        typer.echo(
            f"blinded review of {experiment_dir} at http://{bound_host}:{bound_port}/ "
            f"(reviewer {resolved}; do NOT open the operator view for this "
            "experiment; Ctrl-C to stop)"
        )
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            typer.echo("reviewer surface stopped")
        finally:
            srv.server_close()

    app.add_typer(review_app, name="review")
