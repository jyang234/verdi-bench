"""Real-container proof that a declared holdout grades end-to-end [refactor 05 §1].

The offline suite (``test_eval5_holdouts``) proves the ``run_holdouts`` entrypoint
against monkeypatched mounts; this proves it against a LIVE daemon: a python image
that bundles the harness and runs ``python -m harness.grade.run_holdouts`` as its
entrypoint — exactly the shipped-grader-image shape (refactor 03 §3), the same
mechanism ``run_plugin`` uses. verdi mounts the materialized ``AssertionHoldout``
read-only at ``/holdouts``, the entrypoint executes it against ``/workspace``, and
its result rides the nonce-authenticated fenced stdout channel at the trusted
(``grader=docker``) tier. python-image based (no gcc); skips without a daemon.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness.grade.container import DockerGradeRunner, GradingContainer
from harness.grade.deterministic import grade_trial
from harness.grade.holdouts import AssertionHoldout
from harness.grade.types import GradeTask
from harness.ledger.events import EventContext
from harness.ledger.query import find_events
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_REPO = Path(__file__).resolve().parents[1]
_IMAGE = "verdi-bench/run-holdouts-e2e:latest"
_ADD5 = "from solution import add; assert add(2, 3) == 5"


def _build_grader_image(tmp_path: Path) -> None:
    """A python image that bundles the harness and runs run_holdouts as its
    entrypoint — the generic shipped-grader shape. Only pydantic + pyyaml are
    needed (the run_holdouts import chain pulls in nothing heavier)."""
    ctx = tmp_path / "img"
    ctx.mkdir()
    shutil.copytree(
        _REPO / "harness", ctx / "harness",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (ctx / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "COPY harness /app/harness\n"
        "RUN pip install --no-cache-dir pydantic pyyaml\n"
        "ENV PYTHONPATH=/app\n"
        "ENV PYTHONDONTWRITEBYTECODE=1\n"
        'ENTRYPOINT ["python", "-m", "harness.grade.run_holdouts"]\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["docker", "build", "-t", _IMAGE, str(ctx)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"grader image build failed:\n{proc.stderr}"


def _grade(image: str, tmp_path: Path, name: str, solution: str) -> dict:
    hd = tmp_path / name / "holdouts"
    AssertionHoldout(expression=_ADD5).materialize(hd)
    ws = tmp_path / name / "ws"
    ws.mkdir(parents=True)
    (ws / "solution.py").write_text(solution, encoding="utf-8")
    ledger = tmp_path / name / "l.ndjson"
    grade_trial(
        f"trial-{name}", GradeTask(id="t", task_sha="s", holdouts_dir=str(hd)),
        ws, ledger, EventContext(experiment_id="e", clock=lambda: "t"),
        container=GradingContainer(runner=DockerGradeRunner(), image=image),
    )
    grades = find_events(ledger, "grade")
    assert grades, f"no grade recorded; cant_grade={find_events(ledger, 'cant_grade')}"
    return grades[0]


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_docker_run_holdouts_grades_declared_assertion(tmp_path):
    _build_grader_image(tmp_path)

    # a correct solution: the container executes the holdout and scores a PASS at
    # the trusted (grader=docker) tier — verdi delivered the holdout to the
    # network-less grading container and read only the fenced stdout.
    good = _grade(_IMAGE, tmp_path, "good", "def add(a, b):\n    return a + b\n")
    assert good["binary_score"] is True
    assert good["grader"] == "docker"
    assert [a["source"] for a in good["assertions"]] == ["holdout_test"]
    assert [a["id"] for a in good["assertions"]] == ["h1"]

    # a wrong solution FAILS — proof the container really executed the holdout
    # against the workspace rather than rubber-stamping it.
    bad = _grade(_IMAGE, tmp_path, "bad", "def add(a, b):\n    return a + b + 1\n")
    assert bad["binary_score"] is False
    assert bad["grader"] == "docker"
