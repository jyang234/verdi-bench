"""Grading-container launcher [EVAL-5 §M1].

A thin, **network-less** specialization of EVAL-4's container plumbing. Trial
containers are never reused — grading runs in a fresh container per trial with a
copy of the trial's final workspace and the holdouts bind-mounted **read-only**.

Like the Harbor engine, the docker-run command is built purely (unit-testable);
daemon calls sit behind an injectable runner so the network/readonly assertions
can be checked without a live daemon (true container-inspect is docker-marked).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


class GradingContainerError(RuntimeError):
    """Container/daemon failure during grading → cant_grade(container_failure)."""


@dataclass
class HoldoutRun:
    raw_output: dict
    exit_status: int = 0


class GradeRunner(Protocol):
    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun: ...


class DockerGradeRunner:
    """Runs holdouts in a fresh network-less container via the docker CLI."""

    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.SubprocessError) as e:
            raise GradingContainerError(str(e)) from e
        if proc.returncode == 125:
            raise GradingContainerError("docker daemon/config error")
        # the container writes results to <workspace>/holdout_results.json
        results = workspace / "holdout_results.json"
        if not results.exists():
            raise GradingContainerError("no holdout_results.json produced")
        try:
            return HoldoutRun(json.loads(results.read_text(encoding="utf-8")), proc.returncode)
        except json.JSONDecodeError as e:
            # malformed output is surfaced distinctly by the caller
            raise ValueError(f"malformed holdout output: {e}") from e


class LocalGradeRunner:
    """No-daemon runner: reads a pre-placed ``holdout_results.json`` from the
    workspace. Used by the fake/end-to-end path so grading is exercisable without
    Docker (the real DockerGradeRunner is docker-marked)."""

    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun:
        results = Path(workspace) / "holdout_results.json"
        if not results.exists():
            raise GradingContainerError("no holdout_results.json in workspace")
        try:
            return HoldoutRun(json.loads(results.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            # surface as a HoldoutRun with a marker so the parser flags malformed
            return HoldoutRun({"__malformed__": True})


class GradingContainer:
    def __init__(self, runner: Optional[GradeRunner] = None):
        self._runner = runner or DockerGradeRunner()

    def build_grade_command(self, workspace: Path, holdouts_dir: str) -> list[str]:
        """Fresh, network-less container; holdouts read-only."""
        cmd = ["docker", "run", "--rm", "--network", "none"]
        cmd += ["--volume", f"{Path(workspace).resolve()}:/workspace"]
        if holdouts_dir:
            # holdouts bind-mounted READ-ONLY [AC-1]
            cmd += ["--volume", f"{Path(holdouts_dir).resolve()}:/holdouts:ro"]
        cmd += ["--workdir", "/workspace", "verdi-bench/grader@sha256:" + "0" * 64]
        return cmd

    def run(self, workspace: Path, holdouts_dir: str) -> HoldoutRun:
        cmd = self.build_grade_command(workspace, holdouts_dir)
        return self._runner.run_holdouts(cmd, Path(workspace), holdouts_dir)
