"""Real-container proof of the groundwork grader backend [integration plan §3 A1].

The four docker-marked cases the plan requires, run against a LIVE daemon with the
real grader image (flowmap + groundwork baked in):

  (a) a planted-violation workspace produces a ``failed`` groundwork rule assertion
      AND a failing ``verdi-groundwork-check`` command holdout (binary_score False);
  (b) the reference solution passes (command holdout True, no failed rule);
  (c) a blind-spot task ``abstain``s without touching the binary score;
  (d) the binary absent from the image → terminal ``cant_grade(plugin_error)``,
      never a silent empty vector.

Doubly gated: needs a docker daemon AND the pinned binaries (VERDI_FLOWMAP_BIN /
VERDI_GROUNDWORK_BIN) to bake into the image via the prebuilt fallback — the grade
container is ``--network none``, so it cannot ``go install`` at build time here.
Build the sibling binaries first (see tests/fixtures/groundwork/regen.sh).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.grade.container import DockerGradeRunner, GradingContainer
from harness.grade.deterministic import grade_trial
from harness.grade.holdouts import AssertionHoldout
from harness.grade.types import AssertionResult, GradeTask
from harness.ledger.events import EventContext
from harness.ledger.query import find_events
from tests.fixtures.docker import DOCKER_AVAILABLE
from tests.fixtures.groundwork_fixtures import INVSVC_DIR

pytestmark = pytest.mark.docker

_REPO = Path(__file__).resolve().parents[1]
_GRADER_IMAGE = "verdi-bench/grader-groundwork-e2e:latest"
_NOBIN_IMAGE = "verdi-bench/grader-nobinaries-e2e:latest"

_FLOWMAP_BIN = os.environ.get("VERDI_FLOWMAP_BIN")
_GROUNDWORK_BIN = os.environ.get("VERDI_GROUNDWORK_BIN")
_BINARIES_SET = bool(_FLOWMAP_BIN and _GROUNDWORK_BIN)

_needs_daemon = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
_needs_binaries = pytest.mark.skipif(
    not _BINARIES_SET,
    reason="set VERDI_FLOWMAP_BIN and VERDI_GROUNDWORK_BIN (sibling-built) to bake "
    "the grader image via the prebuilt fallback",
)


def _build_grader_image(tmp_path: Path) -> None:
    """Build the real grader image from the committed Dockerfile via the prebuilt
    fallback: assemble a build context and drop the sibling binaries into bin/."""
    ctx = tmp_path / "ctx"
    (ctx / "images" / "grader" / "bin").mkdir(parents=True)
    shutil.copytree(_REPO / "harness", ctx / "harness",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("Dockerfile", "verdi-groundwork-check", "README.md"):
        shutil.copy(_REPO / "images" / "grader" / name, ctx / "images" / "grader" / name)
    shutil.copy(_FLOWMAP_BIN, ctx / "images" / "grader" / "bin" / "flowmap")
    shutil.copy(_GROUNDWORK_BIN, ctx / "images" / "grader" / "bin" / "groundwork")
    proc = subprocess.run(
        ["docker", "build", "-f", "images/grader/Dockerfile",
         "--build-arg", "GROUNDWORK_PREBUILT=1", "-t", _GRADER_IMAGE, str(ctx)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"grader image build failed:\n{proc.stderr[-3000:]}"


def _build_nobinaries_image(tmp_path: Path) -> None:
    """A grader image WITHOUT flowmap/groundwork (case d). Uses CMD run_holdouts so
    the plugin command can override it — exactly the real grader's shape, minus the
    toolchain."""
    ctx = tmp_path / "nobin"
    ctx.mkdir()
    shutil.copytree(_REPO / "harness", ctx / "harness",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (ctx / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "COPY harness /app/harness\n"
        "RUN pip install --no-cache-dir pydantic pyyaml\n"
        "ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1\n"
        'CMD ["python", "-m", "harness.grade.run_holdouts"]\n',
        encoding="utf-8",
    )
    proc = subprocess.run(["docker", "build", "-t", _NOBIN_IMAGE, str(ctx)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"no-binaries image build failed:\n{proc.stderr[-2000:]}"


def _command_holdout(hd: Path, task_id: str) -> None:
    (hd / "holdout.json").write_text(json.dumps({
        "schema_version": 1, "kind": "command", "id": "groundwork-gate",
        "argv": ["/usr/local/bin/verdi-groundwork-check", task_id],
    }, sort_keys=True, indent=2), encoding="utf-8")


def _holdouts(tmp_path: Path, svc_dir: Path) -> Path:
    """A holdouts dir carrying the groundwork policy + base graph assets."""
    hd = tmp_path / "holdouts"
    (hd / "groundwork").mkdir(parents=True)
    src = svc_dir / "holdouts" / "groundwork"
    shutil.copy(src / "policy.json", hd / "groundwork" / "policy.json")
    shutil.copy(src / "base.graph.json", hd / "groundwork" / "base.graph.json")
    return hd


def _grade(image: str, tmp_path: Path, workspace_src: Path, hd: Path, task_id: str) -> dict | None:
    ws = tmp_path / "ws"
    shutil.copytree(workspace_src, ws)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id=task_id, task_sha=f"{task_id}-e2e",
                     holdouts_dir=str(hd), plugin_ids=["groundwork"])
    grade_trial(
        f"trial-{task_id}", task, ws, ledger,
        EventContext(experiment_id="e", clock=lambda: "t"),
        container=GradingContainer(runner=DockerGradeRunner(), image=image),
    )
    grades = find_events(ledger, "grade")
    if not grades:
        return None
    return grades[0]


def _plugin_rules(grade: dict) -> dict:
    return {a["id"]: a["result"] for a in grade["assertions"] if a["source"] == "plugin:groundwork"}


@_needs_daemon
@_needs_binaries
def test_grader_image_bakes_the_binaries(tmp_path):
    """Deliverable 3: the image build succeeds and flowmap/groundwork are present."""
    _build_grader_image(tmp_path)
    for bin_name in ("flowmap", "groundwork"):
        proc = subprocess.run(["docker", "run", "--rm", "--entrypoint", bin_name,
                               _GRADER_IMAGE, "version"], capture_output=True, text=True)
        assert proc.returncode == 0, f"{bin_name} version failed:\n{proc.stderr}"
        assert bin_name in proc.stdout


@_needs_daemon
@_needs_binaries
def test_case_a_planted_violation_flags_rule_and_fails_command_holdout(tmp_path):
    _build_grader_image(tmp_path)
    hd = _holdouts(tmp_path, INVSVC_DIR)
    _command_holdout(hd, "invsvc")
    grade = _grade(_GRADER_IMAGE, tmp_path, INVSVC_DIR / "violating", hd, "invsvc")
    assert grade is not None, "expected a grade, not a cant_grade"
    assert _plugin_rules(grade).get("must_not_reach") == AssertionResult.failed.value
    # the command holdout (binary gate) failed → binary score is False
    assert grade["binary_score"] is False


@_needs_daemon
@_needs_binaries
def test_case_b_reference_solution_passes(tmp_path):
    _build_grader_image(tmp_path)
    hd = _holdouts(tmp_path, INVSVC_DIR)
    _command_holdout(hd, "invsvc")
    grade = _grade(_GRADER_IMAGE, tmp_path, INVSVC_DIR / "reference", hd, "invsvc")
    assert grade is not None
    rules = _plugin_rules(grade)
    assert AssertionResult.failed.value not in rules.values(), rules
    assert grade["binary_score"] is True  # command holdout passed


@_needs_daemon
@_needs_binaries
def test_case_c_blind_spot_abstains_without_touching_binary_score(tmp_path):
    _build_grader_image(tmp_path)
    blindspot = INVSVC_DIR / "blindspot"
    hd = _holdouts(tmp_path, blindspot)
    _command_holdout(hd, "alertsvc")
    grade = _grade(_GRADER_IMAGE, tmp_path, blindspot, hd, "alertsvc")
    assert grade is not None
    rules = _plugin_rules(grade)
    assert AssertionResult.abstain.value in rules.values(), rules
    assert AssertionResult.failed.value not in rules.values(), rules
    # the caution does not block the gate → the command holdout passes → binary True
    assert grade["binary_score"] is True


@_needs_daemon
@_needs_binaries
def test_case_d_binary_absent_is_terminal_cant_grade_not_silent_empty(tmp_path):
    _build_nobinaries_image(tmp_path)
    hd = _holdouts(tmp_path, INVSVC_DIR)
    # a trivially-passing functional holdout so the holdout tier succeeds and the
    # ONLY failure is the plugin's missing toolchain.
    AssertionHoldout(expression="assert True", id="h1").materialize(hd)
    ws = tmp_path / "ws"
    shutil.copytree(INVSVC_DIR / "violating", ws)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="invsvc", task_sha="invsvc-e2e",
                     holdouts_dir=str(hd), plugin_ids=["groundwork"])
    out = grade_trial(
        "trial-nobin", task, ws, ledger,
        EventContext(experiment_id="e", clock=lambda: "t"),
        container=GradingContainer(runner=DockerGradeRunner(), image=_NOBIN_IMAGE),
    )
    # terminal cant_grade(plugin_error) — NEVER a grade with a silent empty vector
    assert out.graded is False
    assert not find_events(ledger, "grade")
    assert find_events(ledger, "cant_grade")[0]["reason"] == "plugin_error"
