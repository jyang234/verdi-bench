"""End-to-end Phase-1 pipeline coverage [XC-1].

``test_fake_pipeline_*`` drives plan -> run (fake engine) -> grade (local) through
the ``bench`` CLI with no daemon, exercising the task commitment and the grade
runner selection end to end.

``test_docker_grade_real_container`` is the first ``docker``-marked test to
actually run a grading container: it builds a minimal grader image, grades a
real trial, and proves the evidence-safe fresh-copy path (a forged all-pass
results file is ignored and the original workspace is untouched). It is skipped
where no daemon is present and is meant for a labelled/scheduled CI job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger.query import find_events
from tests.fixtures.builders import write_experiment_yaml

runner = CliRunner()


def _setup(expdir: Path, tasks: list) -> Path:
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml")
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    return expdir / "ledger.ndjson"


def test_fake_pipeline_plan_run_grade(tmp_path):
    expdir = tmp_path / "exp"
    ledger = _setup(
        expdir,
        [{"id": "t1", "prompt": "solve", "fake_behavior": {"native_log": {"total_cost_usd": 0.01}}}],
    )

    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    r = runner.invoke(app, ["run", str(expdir)])
    assert r.exit_code == 0, r.output
    trials = find_events(ledger, "trial")
    assert trials, "the fake run produced no trials"

    # Stand in for the grader container output the local runner reads.
    for ev in trials:
        ws = Path(ev["trial_record"]["artifacts_path"]).parent
        (ws / "holdout_results.json").write_text(
            json.dumps({"assertions": [{"id": "h1", "result": "pass"}]}), encoding="utf-8"
        )

    r2 = runner.invoke(app, ["grade", str(expdir), "--runner", "local"])
    assert r2.exit_code == 0, r2.output
    grades = find_events(ledger, "grade")
    assert len(grades) == len(trials)
    assert all(g["binary_score"] is True for g in grades)
    # the whole ledger still verifies after the full pipeline
    assert runner.invoke(app, ["verify-chain", str(ledger)]).exit_code == 0


# --- docker-marked: the real grading container -----------------------------
def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=15
        ).returncode == 0
    except Exception:
        return False


DOCKER_AVAILABLE = _docker_available()


@pytest.mark.docker
@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_docker_grade_real_container(tmp_path):
    """Grade a trial in a real container; the fresh-copy path must ignore a
    forged results file and leave the ledgered evidence untouched (GR-1/GR-3)."""
    from harness.grade.container import DockerGradeRunner, GradingContainer
    from harness.grade.deterministic import grade_trial
    from harness.grade.types import GradeTask
    from harness.ledger.events import EventContext

    # A minimal grader image that writes a FAIL result — no shell escaping.
    ctx_dir = tmp_path / "img"
    ctx_dir.mkdir()
    (ctx_dir / "results.json").write_text(
        json.dumps({"assertions": [{"id": "h1", "result": "fail"}]}), encoding="utf-8"
    )
    (ctx_dir / "Dockerfile").write_text(
        "FROM busybox\n"
        "COPY results.json /results.json\n"
        'CMD ["cp", "/results.json", "/workspace/holdout_results.json"]\n',
        encoding="utf-8",
    )
    image = "verdi-bench/grader-e2e:latest"
    subprocess.run(
        ["docker", "build", "-t", image, str(ctx_dir)], check=True, capture_output=True
    )

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "solution.txt").write_text("agent output", encoding="utf-8")
    # the subject agent forges an all-pass file in its own workspace
    (ws / "holdout_results.json").write_text(
        json.dumps({"assertions": [{"id": "h1", "result": "pass"}]}), encoding="utf-8"
    )

    ledger = tmp_path / "l.ndjson"
    container = GradingContainer(runner=DockerGradeRunner(), image=image)
    grade_trial(
        "trial-d", GradeTask(id="t", task_sha="s"), ws, ledger,
        EventContext(experiment_id="e", clock=lambda: "t"), container=container,
    )

    grades = find_events(ledger, "grade")
    assert len(grades) == 1
    # the container's FAIL output was scored, not the forged PASS file
    assert grades[0]["binary_score"] is False
    # the original workspace (ledgered evidence) is untouched
    assert (ws / "solution.txt").read_text(encoding="utf-8") == "agent output"
    assert json.loads((ws / "holdout_results.json").read_text())["assertions"][0]["result"] == "pass"
