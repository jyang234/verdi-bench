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
    # local grades are stamped ADVISORY so they cannot masquerade as trusted
    # container grades in an audit [SEC].
    assert all(g.get("grader") == "local" for g in grades)
    # the whole ledger still verifies after the full pipeline
    assert runner.invoke(app, ["verify-chain", str(ledger)]).exit_code == 0


def test_retry_terminal_override_regrades_and_discloses(tmp_path):
    """7B-2/D-P7-2: --retry-terminal re-attempts a terminal cant_grade, stamps
    override_of on the resulting grade, and the findings disclose the override
    count. A --retry-terminal on an already-graded trial is refused."""
    from harness.analyze.report import compute_findings, render_markdown
    from harness.ledger.query import ledger_head_hash
    from harness.schema.experiment import ExperimentSpec

    expdir = tmp_path / "exp"
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml", repetitions=1)
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "solve",
                                   "fake_behavior": {"native_log": {"total_cost_usd": 0.01}}}]}),
        encoding="utf-8",
    )
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    assert runner.invoke(app, ["run", str(expdir)]).exit_code == 0
    trials = find_events(ledger, "trial")
    assert trials

    # First grade with NO holdout_results.json present ⇒ terminal container_failure.
    r1 = runner.invoke(app, ["grade", str(expdir), "--runner", "local"])
    assert r1.exit_code == 0, r1.output
    cant = find_events(ledger, "cant_grade")
    assert cant and all(c["reason"] == "container_failure" for c in cant)
    target = cant[0]["trial_id"]

    # Now place the results the local runner reads, and override the target only.
    for ev in trials:
        ws = Path(ev["trial_record"]["artifacts_path"]).parent
        (ws / "holdout_results.json").write_text(
            json.dumps({"assertions": [{"id": "h1", "result": "pass"}]}), encoding="utf-8"
        )
    r2 = runner.invoke(
        app, ["grade", str(expdir), "--runner", "local", "--retry-terminal", target]
    )
    assert r2.exit_code == 0, r2.output

    grades = find_events(ledger, "grade")
    assert [g["trial_id"] for g in grades] == [target]
    assert len(grades[0]["override_of"]) == 64  # sha256 line hash of the cant_grade

    # Overriding a now-graded trial is refused.
    r3 = runner.invoke(
        app, ["grade", str(expdir), "--runner", "local", "--retry-terminal", target]
    )
    assert r3.exit_code == 2
    assert "already has a grade" in (r3.output + (r3.stderr or ""))

    # The findings disclose the override count in both renders.
    spec = ExperimentSpec.from_yaml(expdir / "experiment.yaml")
    findings = compute_findings(ledger, spec, spec.seed, coverage_n_sim=20, n_boot=200)
    assert findings.overrides["n_override_events"] == 1
    md = render_markdown(findings, ledger, "exploratory")
    assert "override-graded" in md and "Terminal overrides" in md


def test_run_refuses_quarantined_task_version(tmp_path):
    """RN-5 + D-2: bench run loads the flake quarantine from the ledger and
    refuses to run a task version with a quarantining baseline — no trials run."""
    from harness.corpus.commit import load_task_dicts, task_content_sha
    from harness.ledger.events import EventContext, record_flake_baseline

    expdir = tmp_path / "exp"
    ledger = _setup(expdir, [{"id": "t1", "prompt": "solve", "fake_behavior": {"native_log": {}}}])
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0

    # quarantine the exact task version bench run will compute for t1
    sha = task_content_sha(load_task_dicts(expdir)[0])
    record_flake_baseline(
        ledger, EventContext(experiment_id="exp", clock=lambda: "t"),
        task_id="t1", task_sha=sha, k=5,
        results=[{"run": i, "passed": False} for i in range(5)], verdict="quarantined",
    )

    r = runner.invoke(app, ["run", str(expdir)])
    assert r.exit_code != 0  # refused
    assert find_events(ledger, "trial") == []  # nothing ran


# --- docker-marked: the real grading container -----------------------------
from tests.fixtures.docker import DOCKER_AVAILABLE  # noqa: E402


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
