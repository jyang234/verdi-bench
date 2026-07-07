"""``bench forensics`` — scan, human spot-check, operator quarantine [EVAL-11].

Thin typer verbs over the stage API (:mod:`harness.forensics.api`): ``scan``
appends exactly one ``forensics_report``; ``record`` ledgers a human's
per-detector spot-check [AC-4, D006]; ``quarantine`` ledgers the operator
disposition [D003, D007] — the only path by which forensics affects a
comparison, and it is a human act, never a detector's. The one-event property
registration fires here at import time [EVAL-3 §M7, XC-3].
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..cli_common import event_context, refusal_exit
from ..ledger.events import EventContext
from .api import forensics_record, forensics_scan, quarantine
from .detectors import DETECTOR_IDS
from .scan import UnknownTrialError, run_forensics


def register(app: typer.Typer) -> None:
    forensics_app = typer.Typer(
        help="Transcript forensics: metrics, gaming detectors, advisory review [EVAL-11].",
        no_args_is_help=True,
    )

    @forensics_app.command("scan")
    def scan_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml"),
        review: bool = typer.Option(
            True, "--review/--no-review",
            help="Run the blinded advisory LLM pass (fails closed to CANT_REVIEW)",
        ),
        model: str = typer.Option(
            None, "--model", help="Provider model for the review (default: judge model)"
        ),
        actor: str = typer.Option(None, "--actor", help="Actor on the report event [GR-12]"),
    ) -> None:
        """Scan every trial; append exactly one forensics_report event."""
        ctx = event_context(experiment_dir, actor)
        outcome = forensics_scan(experiment_dir, ctx=ctx, review=review, model=model)
        typer.echo(
            f"forensics: {outcome.covered}/{outcome.trials} trial(s) covered, "
            f"{outcome.n_flags} flag(s), "
            f"{outcome.n_gaps} coverage gap(s)"
        )

    @forensics_app.command("record")
    def record_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        trial_id: str = typer.Option(..., "--trial-id"),
        labels_json: Path = typer.Option(
            ..., "--labels", help='JSON {"<detector_id>": true|false, ...}'
        ),
        stratum: str = typer.Option(
            "mandatory", "--stratum", help="EVAL-7 review stratum: mandatory | floor"
        ),
        actor: str = typer.Option(None, "--actor", help="Human reviewer identity [GR-12]"),
    ) -> None:
        """Record a human per-detector spot-check [AC-4, D006]."""
        labels = json.loads(labels_json.read_text(encoding="utf-8"))
        unknown = sorted(set(labels) - set(DETECTOR_IDS))
        if unknown or not labels or not all(isinstance(v, bool) for v in labels.values()):
            typer.echo(
                f"labels must map known detector ids {sorted(DETECTOR_IDS)} to booleans; "
                f"got unknown={unknown} in {labels}",
                err=True,
            )
            raise typer.Exit(code=2)
        ctx = event_context(experiment_dir, actor)
        forensics_record(
            experiment_dir, ctx=ctx, trial_id=trial_id, labels=labels, stratum=stratum
        )
        typer.echo(f"recorded forensic spot-check for {trial_id}")

    @forensics_app.command("quarantine")
    def quarantine_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        trial_id: str = typer.Option(..., "--trial-id"),
        reason: str = typer.Option(..., "--reason", help="Why this trial is excluded"),
        actor: str = typer.Option(None, "--actor", help="Operator identity [GR-12]"),
    ) -> None:
        """Ledger the operator disposition: exclude a trial, disclosed [D007]."""
        ctx = event_context(experiment_dir, actor)
        with refusal_exit(UnknownTrialError):
            quarantine(experiment_dir, ctx=ctx, trial_id=trial_id, reason=reason)
        typer.echo(f"quarantined {trial_id} (excluded from comparisons, disclosed)")

    app.add_typer(forensics_app, name="forensics")


# --- one-event property registration [EVAL-3 §M7, XC-3] ----------------------
def _prepare_forensics(ctx_dir: str) -> None:
    from ..plan.lock import lock_experiment

    d = Path(ctx_dir)
    lock_experiment(
        d / "experiment.yaml", d / "ledger.ndjson",
        ctx=EventContext(experiment_id="prop"), n_sim=8, n_boot=40, deltas=[0.2, 0.4],
    )


def _forensics_entrypoint(ctx_dir: str) -> None:
    # A scan over a trial-less ledger is a full-coverage-of-nothing report —
    # still exactly one forensics_report event (deterministic tier only; the
    # advisory pass needs no provider when there is nothing to review).
    d = Path(ctx_dir)
    run_forensics(d, ctx=EventContext(experiment_id="prop"), review=False)


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("forensics", _forensics_entrypoint, prepare=_prepare_forensics)


_register()
