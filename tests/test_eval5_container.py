"""EVAL-5 AC-1 — grading isolation: no network, holdouts read-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.grade.container import GradingContainer


def test_ac1_grading_isolated(tmp_path):
    """The grading container command denies the network namespace."""
    cmd = GradingContainer().build_grade_command(tmp_path / "ws", str(tmp_path / "holdouts"))
    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"


def test_ac1_holdouts_readonly(tmp_path):
    """Holdouts are bind-mounted read-only (`:ro`)."""
    holdouts = tmp_path / "holdouts"
    cmd = GradingContainer().build_grade_command(tmp_path / "ws", str(holdouts))
    mounts = [cmd[i + 1] for i, t in enumerate(cmd) if t == "--volume"]
    holdout_mounts = [m for m in mounts if "/holdouts" in m]
    assert holdout_mounts and all(m.endswith(":ro") for m in holdout_mounts)
    # the workspace mount is NOT read-only (grading needs the agent's output)
    ws_mounts = [m for m in mounts if m.endswith("/workspace")]
    assert ws_mounts and not any(m.endswith(":ro") for m in ws_mounts)


def test_ac1_fresh_container_not_reused(tmp_path):
    """--rm ensures a fresh container each grade (trial containers never reused)."""
    cmd = GradingContainer().build_grade_command(tmp_path / "ws", "")
    assert "--rm" in cmd


def test_grader_image_configurable_not_placeholder(tmp_path):
    """GR-4: the grader image is configurable, not a hardcoded all-zeros digest
    (a nonexistent placeholder Docker could never pull)."""
    pinned = "verdi-bench/grader@sha256:" + "a" * 64
    cmd = GradingContainer(image=pinned).build_grade_command(tmp_path / "ws", "")
    assert pinned in cmd
    assert ("verdi-bench/grader@sha256:" + "0" * 64) not in cmd


def test_docker_runner_gates_nonzero_exit(tmp_path, monkeypatch):
    """GR-2: any nonzero exit (not just 125) is a container failure — the runner
    must NOT fall through to scoring a stale/forged workspace file."""
    import subprocess

    from harness.grade.container import DockerGradeRunner, GradingContainerError

    ws = tmp_path / "ws"
    ws.mkdir()
    # a forged results file is present; exit 137 must still refuse
    (ws / "holdout_results.json").write_text('{"assertions": []}', encoding="utf-8")

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 137, "", "OOM")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GradingContainerError):
        DockerGradeRunner().run_holdouts(["docker", "run"], ws, "")


def test_docker_runner_malformed_output_fails_closed(tmp_path, monkeypatch):
    """GR-6: malformed holdout JSON on the docker path must not raise a bare
    ValueError that escapes grade_trial — it flows to cant_grade(malformed)."""
    import subprocess

    from harness.grade.container import DockerGradeRunner
    from harness.grade.deterministic import MalformedHoldoutOutput, parse_holdout_output

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "holdout_results.json").write_text("{not json", encoding="utf-8")

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run = DockerGradeRunner().run_holdouts(["docker", "run"], ws, "")
    # the runner returns a malformed marker rather than raising a bare ValueError;
    # parsing it fails closed with the module's typed error.
    with pytest.raises(MalformedHoldoutOutput):
        parse_holdout_output(run.raw_output)
