"""``bench contamination`` subcommands [EVAL-10 AC-3] — thin shell over
:mod:`harness.contamination.api`.

``probe`` is the story's orchestration point (references loaded once, AC-4
overlap scan, AC-3 memory probes, one ledgered ``contamination_probe`` event);
the CLI resolves the actor, maps the overlap refusal to exit 2, echoes the scan
alarms + per-arm outcomes, and surfaces a probe refusal (exit 2) / CANT_PROBE
(exit 1). Scan alarms and skipped trials are echoed, never swallowed.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..cli_common import event_context, refusal_exit
from .api import contamination_probe
from .overlap import OverlapError


def register(app: typer.Typer) -> None:
    contamination_app = typer.Typer(
        help="Contamination sentinel: membership probes + overlap scan [EVAL-10].",
        no_args_is_help=True,
    )

    @contamination_app.command("probe")
    def cmd_contamination_probe(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml + ledger.ndjson"),
        manifest_path: Path = typer.Option(
            None, "--manifest",
            help="Corpus manifest supplying task created_at + canary presence",
        ),
        oracle_dir: Path = typer.Option(
            None, "--oracle-dir",
            help="Dir of <task_id>.txt oracle solutions (when the corpus carries them)",
        ),
        scan_artifacts: bool = typer.Option(
            True, "--scan-artifacts/--no-scan-artifacts",
            help="Run the deterministic overlap scan over ledgered trial artifacts [AC-4]",
        ),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the probe [GR-12]"),
    ) -> None:
        """Probe every arm model for training-set membership [AC-3, D002]."""
        ctx = event_context(experiment_dir, actor)
        with refusal_exit(OverlapError):
            outcome = contamination_probe(
                experiment_dir, ctx=ctx, manifest_path=manifest_path,
                oracle_dir=oracle_dir, scan_artifacts=scan_artifacts,
            )
        for alarm in outcome.alarms:
            typer.echo(f"INSULATION ALARM [EVAL-4 AC-9]: {alarm}", err=True)
        for skip in outcome.skipped:
            typer.echo(f"UNSCANNED: {skip}", err=True)
        if outcome.probe_error is not None:
            typer.echo(outcome.probe_error, err=True)
            raise typer.Exit(code=2)
        probe = outcome.probe
        if probe["status"] != "complete":
            typer.echo(
                f"CANT_PROBE({probe['reason']}) — ledgered; no partial LLM "
                "outcomes (deterministic overlap flags preserved on the event)",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"contamination probe complete (threshold={probe['threshold']})")
        for arm, payload in probe["arms"].items():
            flagged = sorted(
                tid for tid, st in payload["outcomes"].items() if st == "flagged"
            )
            typer.echo(f"  {arm}: flagged={json.dumps(flagged)}")

    app.add_typer(contamination_app, name="contamination")
