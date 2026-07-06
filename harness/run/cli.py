"""``bench run`` [EVAL-4 §M6] — thin shell over :mod:`harness.run.api`.

Parses the flags, maps the enumerated refusals to exit codes, and echoes the
counts; the execution logic (lock assertion, task resolution, interleave,
schedule) lives in the stage API [refactor 02 §3].
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..cli_common import refusal_exit
from ..corpus.commit import TaskCommitmentError
from ..ledger.actor import ActorResolutionError
from ..plan.lock import LockError
from .api import (
    CorpusManifestMismatchError,
    NoTasksError,
    export_control_bundle,
    run_experiment,
)
from .control_reuse import ControlReuseError
from .reuse import ControlBundleError


def register(app: typer.Typer) -> None:
    @app.command()
    def run(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        engine: str = typer.Option("fake", "--engine", help="fake | harbor"),
        corpus_manifest: Path = typer.Option(
            None, "--corpus-manifest", help="Manifest gating schedulability (is_schedulable) [CO-2]"
        ),
        actor: str = typer.Option(
            None, "--actor", help="Actor recorded on the trial events [GR-12]"
        ),
        reuse_control: Path = typer.Option(
            None,
            "--reuse-control",
            help="Path to a control bundle to reuse instead of running the "
            "control arm (exploratory-only; preflight refuses on any drift)",
        ),
    ) -> None:
        """Execute the locked experiment's interleaved trials."""
        from ..hermetic.metering import MeteringProxyError

        try:
            with refusal_exit(
                TaskCommitmentError, CorpusManifestMismatchError,
                ControlReuseError, ControlBundleError, ActorResolutionError,
            ):
                outcome = run_experiment(
                    experiment_dir, engine=engine, corpus_manifest=corpus_manifest,
                    actor=actor, reuse_control=reuse_control,
                )
        except NoTasksError as e:
            raise typer.BadParameter(str(e))
        except MeteringProxyError as e:
            # proxy.managed was set but the managed metering proxy could not stand
            # up (e.g. no docker daemon); refuse loudly rather than run unmetered.
            typer.echo(f"RUN ABORTED: managed metering proxy could not start: {e}", err=True)
            raise typer.Exit(code=2)

        if outcome.reused_arm is not None:
            typer.echo(
                f"reusing control arm {outcome.reused_arm!r} from bundle "
                f"({outcome.reused_cells} cells)"
            )
        # PRA-M9-adjacent: a scheduled quarantined task refuses (exit 2), echoed
        # after any reuse notice — exactly as the inline body ordered them.
        if outcome.quarantine_error is not None:
            typer.echo(outcome.quarantine_error, err=True)
            raise typer.Exit(code=2)
        typer.echo(
            f"ran {outcome.n_trials} trials "
            f"(infra_failures={outcome.infra_failures}, "
            f"stopped_cost_ceiling={outcome.stopped_cost_ceiling})"
        )
        if outcome.aborted_proxy_unavailable:
            # PRA-M9: a dead/misconfigured metering proxy aborted the run; exit
            # nonzero so the operator does not mistake a truncated run for a
            # complete one.
            typer.echo(
                "RUN ABORTED: the metering proxy is dead or misconfigured "
                "(proxy_log_missing); trials remaining were not run [PRA-M9]",
                err=True,
            )
            raise typer.Exit(code=2)

    cache_app = typer.Typer(
        help="Control-run reuse bundles [control-reuse plan].", no_args_is_help=True
    )
    app.add_typer(cache_app, name="control-cache")

    @cache_app.command("export")
    def control_cache_export(
        experiment_dir: Path = typer.Argument(
            ..., help="A completed source experiment directory to export from"
        ),
        arm: str = typer.Option(..., "--arm", help="The control arm to export"),
        out: Path = typer.Option(..., "--out", help="Bundle output path"),
    ) -> None:
        """Export a completed run's control arm as a reusable bundle.

        Snapshots each control trial's judged diff while the workspaces are still
        readable, so the bundle survives the source environment being reclaimed.
        """
        with refusal_exit(ControlBundleError, LockError):
            outcome = export_control_bundle(experiment_dir, arm=arm, out=out)
        typer.echo(
            f"exported control bundle: arm {arm!r}, {outcome.n_cells} cell(s) "
            f"-> {out} (sha {outcome.bundle_sha256[:12]}…)"
        )
