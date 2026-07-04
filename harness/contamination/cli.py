"""``bench contamination`` subcommands [EVAL-10 AC-3].

``probe`` is the story's orchestration point: it runs the deterministic AC-4
overlap scan over the run's trial artifacts, then the AC-3 memory probes per
arm model, and ledgers the merged measurement as one ``contamination_probe``
event. Probes run here, never inside trial containers [constraint].
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer


def _resolve_actor_or_exit(actor_flag: Optional[str]) -> str:
    from ..ledger.actor import ActorResolutionError, resolve_actor

    try:
        return resolve_actor(actor_flag)
    except ActorResolutionError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)


def _read_solution(artifacts_path: str) -> Optional[str]:
    """Concatenated text of a trial's artifact files (sorted; skip binaries).

    None when the recorded path no longer exists — an unscanned trial stays
    honestly unmeasured rather than scoring an empty string."""
    root = Path(artifacts_path)
    if not root.is_dir():
        return None
    parts: list[str] = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            parts.append(f.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    if not parts:
        return None
    return "\n".join(parts)


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
        from ..ledger import events
        from ..ledger.events import EventContext
        from ..ledger.query import find_events
        from ..run.seam import HoldoutLeakError
        from ..schema.experiment import ExperimentSpec
        from .overlap import OverlapError, solution_overlap
        from .probe import ProbeError, ProbeTask, run_memory_probe

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(
            experiment_id=experiment_dir.name, actor=_resolve_actor_or_exit(actor)
        )
        spec = ExperimentSpec.from_yaml(experiment_dir / "experiment.yaml")
        task_dicts = load_task_dicts(experiment_dir)
        manifest = (
            CorpusManifest.load(manifest_path) if manifest_path is not None else None
        )

        def _oracle_for(task_id: str) -> Optional[str]:
            if oracle_dir is None:
                return None
            p = oracle_dir / f"{task_id}.txt"
            return p.read_text(encoding="utf-8") if p.exists() else None

        def _holdouts_for(task: dict) -> list[str]:
            rel = task.get("holdouts_dir")
            if not rel:
                return []
            root = experiment_dir / rel
            if not root.is_dir():
                return []
            return [
                p.read_text(encoding="utf-8")
                for p in sorted(root.rglob("*"))
                if p.is_file()
            ]

        tasks: list[ProbeTask] = []
        by_id: dict[str, dict] = {}
        for t in task_dicts:
            entry = manifest.task(t["id"]) if manifest is not None else None
            has_canary = entry is not None and entry.canary_sha256 is not None
            tasks.append(
                ProbeTask(
                    task_id=t["id"],
                    task_sha=(entry.sha if entry is not None else ""),
                    prompt=t.get("prompt", ""),
                    oracle=_oracle_for(t["id"]),
                    has_canary=has_canary,
                )
            )
            by_id[t["id"]] = t
        threshold = (
            spec.contamination.overlap_threshold
            if spec.contamination is not None
            else None
        )

        # Deterministic AC-4 channel: fingerprint each ledgered trial's
        # artifacts against the task's oracle + holdout content. A holdout hit
        # is the EVAL-4 insulation alarm — echoed loudly, recorded as a flag,
        # never swallowed into a mere score.
        overlap_flags: dict[str, dict[str, bool]] = {}
        if scan_artifacts:
            for ev in find_events(ledger_path, events.TRIAL):
                rec = ev["trial_record"]
                task_id, arm_name = rec["task_id"], rec["arm"]
                task = by_id.get(task_id)
                if task is None:
                    continue  # a trial of a task not in tasks.yaml is not ours to score
                oracle = _oracle_for(task_id)
                holdouts = _holdouts_for(task)
                if oracle is None and not holdouts:
                    continue  # nothing the agent could not have produced — unmeasurable
                solution = _read_solution(rec.get("artifacts_path") or "")
                if solution is None:
                    continue  # artifacts gone ⇒ honestly unmeasured, not score 0
                try:
                    result = solution_overlap(
                        solution, oracle=oracle, holdouts=holdouts, threshold=threshold
                    )
                    flagged = result.flagged
                except HoldoutLeakError as e:
                    typer.echo(
                        f"INSULATION ALARM [EVAL-4 AC-9]: trial {rec['trial_id']} "
                        f"(task {task_id}, arm {arm_name}): {e}",
                        err=True,
                    )
                    flagged = True
                except OverlapError as e:
                    typer.echo(str(e), err=True)
                    raise typer.Exit(code=2)
                per_arm = overlap_flags.setdefault(arm_name, {})
                per_arm[task_id] = per_arm.get(task_id, False) or flagged

        try:
            event = run_memory_probe(
                ledger_path, ctx,
                arms=spec.arms, tasks=tasks,
                threshold=threshold, overlap_flags=overlap_flags,
            )
        except (ProbeError, OverlapError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        probe = event["probe"]
        if probe["status"] != "complete":
            typer.echo(
                f"CANT_PROBE({probe['reason']}) — ledgered, no partial outcomes",
                err=True,
            )
            raise typer.Exit(code=1)
        flagged_by_arm = {
            arm: sorted(
                tid for tid, st in payload["outcomes"].items() if st == "flagged"
            )
            for arm, payload in probe["arms"].items()
        }
        typer.echo(f"contamination probe complete (threshold={probe['threshold']})")
        for arm, flagged in flagged_by_arm.items():
            typer.echo(f"  {arm}: flagged={json.dumps(flagged)}")

    app.add_typer(contamination_app, name="contamination")
