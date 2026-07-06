"""``bench contamination`` subcommands [EVAL-10 AC-3].

``probe`` is the story's orchestration point: it loads each task's references
once, runs the deterministic AC-4 overlap scan (:mod:`.scan`) over the run's
trial artifacts, then the AC-3 memory probes per arm model, and ledgers the
merged measurement as one ``contamination_probe`` event. Scan alarms and
skipped trials are echoed, never swallowed. Probes run here, never inside
trial containers [constraint].
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from ..cli_common import event_context


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
        from ..corpus.commit import load_task_dicts
        from ..corpus.registry import CorpusManifest
        from ..plan.lock import assert_lock
        from .overlap import OverlapError
        from .probe import ProbeError, ProbeTask, run_memory_probe
        from .scan import TaskReferences, scan_trials

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = event_context(experiment_dir, actor)
        # PRA-M2: contamination is a ledgered stage, so it must gate on the lock
        # like every other stage — otherwise a post-lock-mutated spec is probed
        # and its results chained. assert_lock chain-verifies and returns the spec
        # parsed from the locked bytes (no second read; PRA-M1).
        spec = assert_lock(experiment_dir / "experiment.yaml", ledger_path).spec
        task_dicts = load_task_dicts(experiment_dir)
        manifest = (
            CorpusManifest.load(manifest_path) if manifest_path is not None else None
        )

        def _oracle_for(task_id: str) -> Optional[str]:
            if oracle_dir is None:
                return None
            p = oracle_dir / f"{task_id}.txt"
            if not p.exists():
                return None
            return p.read_text(encoding="utf-8", errors="replace")

        def _holdouts_for(task: dict) -> tuple[str, ...]:
            rel = task.get("holdouts_dir")
            if not rel:
                return ()
            root = experiment_dir / rel
            if not root.is_dir():
                # a DECLARED holdout dir that is gone is a broken experiment
                # layout — refusing beats silently disabling the leak channel
                raise OverlapError(
                    f"task {task['id']!r} declares holdouts_dir {rel!r} but "
                    f"{root} is not a directory; refusing a scan that would "
                    "silently skip its holdout references"
                )
            return tuple(
                p.read_text(encoding="utf-8", errors="replace")
                for p in sorted(root.rglob("*"))
                if p.is_file()
            )

        # Per-task inputs, loaded exactly once: the probe and the scan must
        # see the same oracle/holdout content.
        tasks: list[ProbeTask] = []
        references: dict[str, TaskReferences] = {}
        try:
            for t in task_dicts:
                entry = manifest.task(t["id"]) if manifest is not None else None
                oracle = _oracle_for(t["id"])
                references[t["id"]] = TaskReferences(
                    oracle=oracle, holdouts=_holdouts_for(t)
                )
                tasks.append(
                    ProbeTask(
                        task_id=t["id"],
                        task_sha=(entry.sha if entry is not None else ""),
                        prompt=t.get("prompt", ""),
                        oracle=oracle,
                        has_canary=entry is not None
                        and entry.canary_sha256 is not None,
                    )
                )
        except OverlapError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        threshold = (
            spec.contamination.overlap_threshold
            if spec.contamination is not None
            else None
        )

        overlap_flags: dict[str, dict[str, bool]] = {}
        scan_alarms: list[str] = []
        scan_skipped: list[str] = []
        if scan_artifacts:
            try:
                report = scan_trials(ledger_path, references, threshold=threshold)
            except OverlapError as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(code=2)
            for alarm in report.alarms:
                typer.echo(f"INSULATION ALARM [EVAL-4 AC-9]: {alarm}", err=True)
            for skip in report.skipped:
                typer.echo(f"UNSCANNED: {skip}", err=True)
            overlap_flags = report.overlap_flags
            scan_alarms = report.alarms
            scan_skipped = report.skipped

        try:
            event = run_memory_probe(
                ledger_path, ctx,
                arms=spec.arms, tasks=tasks,
                threshold=threshold, overlap_flags=overlap_flags,
                alarms=scan_alarms, skipped=scan_skipped,
            )
        except (ProbeError, OverlapError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        probe = event["probe"]
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
