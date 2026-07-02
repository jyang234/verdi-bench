"""``bench grade`` [EVAL-5 §M5].

Asserts the experiment lock first, then grades every ungraded trial in the
ledger, appending exactly one grade/cant_grade event each. Defaults to the local
(no-daemon) grade runner; the true container path is docker-marked.

Fractional scoring is taken from the **lock** (pre-registration), not runtime
config [AC-3].
"""

from __future__ import annotations

import getpass
import hashlib
import json
from pathlib import Path

import typer
import yaml

# import so the groundwork plugin self-registers
from .plugins import groundwork  # noqa: F401


def _task_sha(task: dict) -> str:
    return hashlib.sha256(
        json.dumps(task, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_grade_tasks(experiment_dir: Path) -> dict:
    from .types import GradeTask

    data = yaml.safe_load((experiment_dir / "tasks.yaml").read_text(encoding="utf-8")) or {}
    tasks = {}
    for t in data.get("tasks", []):
        tasks[t["id"]] = GradeTask(
            id=t["id"],
            task_sha=t.get("task_sha") or _task_sha(t),
            holdouts_dir=t.get("holdouts_dir", ""),
            plugin_ids=t.get("plugin_ids", []),
            fake_holdout_output=t.get("fake_holdout_output"),
            fake_plugin_output=t.get("fake_plugin_output", {}),
        )
    return tasks


def register(app: typer.Typer) -> None:
    @app.command()
    def grade(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
    ) -> None:
        """Grade every ungraded trial deterministically."""
        from ..ledger.events import EventContext
        from ..ledger.query import find_events
        from ..plan.lock import assert_lock
        from ..schema.experiment import ExperimentSpec
        from .container import GradingContainer, LocalGradeRunner
        from .deterministic import grade_trial

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        assert_lock(spec_path, ledger_path)
        spec = ExperimentSpec.from_yaml(spec_path)

        grade_tasks = _load_grade_tasks(experiment_dir)
        already = {e["trial_id"] for e in find_events(ledger_path, "grade")}
        already |= {e["trial_id"] for e in find_events(ledger_path, "cant_grade")}

        try:
            actor = getpass.getuser()
        except Exception:
            actor = "unknown"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=actor)
        container = GradingContainer(runner=LocalGradeRunner())

        graded = 0
        for ev in find_events(ledger_path, "trial"):
            rec = ev["trial_record"]
            tid = rec["trial_id"]
            if tid in already:
                continue
            task = grade_tasks.get(rec["task_id"])
            if task is None:
                continue
            workspace = Path(rec["artifacts_path"]).parent
            # fake path: place the scripted holdout output in the workspace
            if task.fake_holdout_output is not None:
                (workspace / "holdout_results.json").write_text(
                    json.dumps(task.fake_holdout_output), encoding="utf-8"
                )
            grade_trial(
                tid, task, workspace, ledger_path, ctx,
                container=container, fractional=spec.fractional_scoring,
            )
            graded += 1
        typer.echo(f"graded {graded} trial(s)")
