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

    @review_app.command("build")
    def review_build(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml"),
        out: Path = typer.Option(None, "--out", help="Packet HTML path [default: <dir>/review_packet.html]"),
    ) -> None:
        """Sample + render the blinded review packet; record the Response↔arm map."""
        from ..corpus.commit import (
            TaskCommitmentError,
            assert_task_commitment,
            load_task_dicts,
        )
        from ..ledger.events import EventContext
        from ..plan.lock import assert_lock
        from ..schema.experiment import ExperimentSpec
        from .build import build_review

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        lock_event = assert_lock(spec_path, ledger_path)
        spec = ExperimentSpec.from_yaml(spec_path)
        task_dicts = load_task_dicts(experiment_dir)
        try:
            assert_task_commitment(
                lock_event, task_dicts,
                corpus_id=spec.corpus.id, semver=spec.corpus.version,
            )
        except TaskCommitmentError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)

        ctx = EventContext(experiment_id=experiment_dir.name, actor=_actor())
        html, n = build_review(ledger_path, spec, task_dicts, ctx, seed=spec.seed)
        out = out or (experiment_dir / "review_packet.html")
        out.write_text(html, encoding="utf-8")
        typer.echo(f"built review packet: {n} comparison(s) -> {out}")

    @review_app.command("record")
    def review_record(
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
    ) -> None:
        """Record a human verdict + integrity answers (strictly pre-reveal).

        The human picks a **response** (1/2) as shown in the packet; the recorded
        winner is translated to the judge's A/B (arm) frame via the comparison's
        ``review_packet_built`` map, so the kappa join is frame-correct (RV-6/RV-9).
        """
        from ..judge.schema import Evidence, Verdict, VerdictProvenance, Winner
        from ..ledger.events import EventContext
        from ..schema.experiment import ExperimentSpec
        from .record import ReviewError, record_human_verdict, review_packet_built_for

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_actor())

        if winner not in ("1", "2", "TIE", "CANT_JUDGE"):
            typer.echo("--winner must be one of: 1 | 2 | TIE | CANT_JUDGE", err=True)
            raise typer.Exit(code=2)

        built = review_packet_built_for(ledger_path, comparison_id)
        if built is None:
            typer.echo(
                f"comparison {comparison_id!r} has no review_packet_built event; "
                "run `review build` before recording a verdict [RV-6]", err=True,
            )
            raise typer.Exit(code=2)
        response_map = built["response_map"]
        task_class = built.get("task_class")

        # Translate the response the human picked into the judge's A/B arm frame:
        # A is spec.arms[0]. actual_arm is the true arm shown as Response 1, which
        # the reviewer's guess is checked against for guess accuracy [RV-6].
        spec = ExperimentSpec.from_yaml(experiment_dir / "experiment.yaml")
        arm_a_name = spec.arms[0].name
        evidence = []
        if winner in ("1", "2"):
            chosen_arm = response_map[winner]
            letter = "A" if chosen_arm == arm_a_name else "B"
            evidence = [Evidence(kind="diff", response=letter, hunk="reviewer-cited")]
        else:
            letter = winner
        actual_arm = response_map["1"]

        prov = VerdictProvenance(
            judge_model="human", rubric_sha256="human", packet_sha256="human",
            call_ids=["human"], orders="single", temperature=0.0, ts=ctx.clock(),
        )
        verdict = Verdict(
            winner=Winner(letter), reason=reason or winner, evidence=evidence,
            provenance=prov, source="human", comparison_id=comparison_id,
            task_class=task_class,
        )
        try:
            record_human_verdict(
                ledger_path, ctx, verdict=verdict, arm_recognized=arm_recognized,
                arm_guess=arm_guess, actual_arm=actual_arm,
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
            rec = reveal_comparison(ledger_path, ctx, comparison_id=comparison_id)
        except RevealError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        identities = rec["revealed"]["arm_identities"]
        typer.echo(f"revealed {comparison_id}: {identities}")

    app.add_typer(review_app, name="review")
