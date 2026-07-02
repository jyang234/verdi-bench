"""``bench run`` [EVAL-4 §M6].

Asserts the experiment lock first, resolves tasks, derives the interleave from
the locked seed, and executes the schedule producing chained trial events and
redacted artifacts. Defaults to the fake engine (fast, hermetic-by-fiat); the
Harbor engine is selected with ``--engine harbor`` and requires local Docker.

Task resolution: EVAL-8 owns corpus import; until it lands, ``bench run`` reads a
``tasks.yaml`` in the experiment dir as the task source (a documented stand-in).
"""

from __future__ import annotations

import getpass
from pathlib import Path

import typer
import yaml

from ..plan.interleave import derive_schedule, enumerate_trials
from ..schema.experiment import ExperimentSpec
from .types import RunConfig, Task


def _load_tasks(experiment_dir: Path) -> list[Task]:
    tasks_file = experiment_dir / "tasks.yaml"
    if not tasks_file.exists():
        raise typer.BadParameter(f"no tasks.yaml in {experiment_dir}")
    data = yaml.safe_load(tasks_file.read_text(encoding="utf-8")) or {}
    tasks = []
    for t in data.get("tasks", []):
        tasks.append(
            Task(
                id=t["id"],
                prompt=t.get("prompt", ""),
                image=t.get("image", Task.__dataclass_fields__["image"].default),
                timeout_s=t.get("timeout_s"),
                holdout_canaries=t.get("holdout_canaries", []),
                fake_behavior=t.get("fake_behavior", {}),
            )
        )
    return tasks


def register(app: typer.Typer) -> None:
    @app.command()
    def run(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        engine: str = typer.Option("fake", "--engine", help="fake | harbor"),
        concurrency: int = typer.Option(1, "--concurrency", help=">1 stamps contention caveat"),
    ) -> None:
        """Execute the locked experiment's interleaved trials."""
        from ..ledger.events import EventContext
        from ..plan.lock import assert_lock
        from .interleave import schedule

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        assert_lock(spec_path, ledger_path)
        spec = ExperimentSpec.from_yaml(spec_path)

        tasks = _load_tasks(experiment_dir)
        task_map = {t.id: t for t in tasks}
        arm_map = {a.name: a for a in spec.arms}

        trials = enumerate_trials(
            [t.id for t in tasks], [a.name for a in spec.arms], spec.repetitions
        )
        order = derive_schedule(spec.seed, trials)

        from .engines import get_engine

        eng = get_engine(engine)
        config = RunConfig(engine=eng, concurrency=concurrency)
        try:
            actor = getpass.getuser()
        except Exception:
            actor = "unknown"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=actor)

        result = schedule(
            order,
            tasks=task_map,
            arms=arm_map,
            workspace_root=experiment_dir / "workspaces",
            ledger_path=ledger_path,
            ctx=ctx,
            config=config,
            cost_ceiling=spec.cost_ceiling.amount,
        )
        typer.echo(
            f"ran {len(result.records)} trials "
            f"(infra_failures={result.infra_failures}, "
            f"stopped_cost_ceiling={result.stopped_cost_ceiling})"
        )
