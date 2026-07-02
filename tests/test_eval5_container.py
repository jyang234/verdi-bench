"""EVAL-5 AC-1 — grading isolation: no network, holdouts read-only."""

from __future__ import annotations

from pathlib import Path

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
