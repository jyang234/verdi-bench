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
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

# The grader image. Configurable via env so a deployment pins a real digest
# (``verdi-bench/grader@sha256:…``); the default tag is a placeholder a real
# install overrides — never the all-zeros non-digest it used to be [GR-4].
DEFAULT_GRADER_IMAGE = "verdi-bench/grader:latest"

# Filename the grader container writes its results to inside /workspace. An
# agent-written file of this name in its own workspace must never be trusted as
# grader output — the docker runner grades a fresh copy with any pre-existing
# copy of this file removed [GR-1].
HOLDOUT_RESULTS = "holdout_results.json"


class GradingContainerError(RuntimeError):
    """Container/daemon failure during grading → cant_grade(container_failure)."""


@dataclass
class HoldoutRun:
    raw_output: dict
    exit_status: int = 0


class GradeRunner(Protocol):
    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun: ...


class DockerGradeRunner:
    """Runs holdouts in a fresh network-less container via the docker CLI.

    ``fresh_workspace_copy`` tells :class:`GradingContainer` to grade a throwaway
    copy of the trial workspace with any pre-existing results file removed, so
    the container produces its own output and cannot mutate ledgered evidence
    [GR-1/GR-3].
    """

    fresh_workspace_copy = True

    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.SubprocessError) as e:
            raise GradingContainerError(str(e)) from e
        # Any nonzero exit means the grader run itself failed — not that holdout
        # tests failed (those are per-assertion in the results file at exit 0).
        # Refuse rather than scoring a stale/partial workspace file [GR-2].
        if proc.returncode != 0:
            detail = "docker daemon/config error" if proc.returncode == 125 else proc.stderr.strip()
            raise GradingContainerError(
                f"grader container exited {proc.returncode}"
                + (f": {detail}" if detail else "")
            )
        results = Path(workspace) / HOLDOUT_RESULTS
        if not results.exists():
            raise GradingContainerError("no holdout_results.json produced")
        try:
            raw = json.loads(results.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Malformed output flows to cant_grade(malformed) via the parser's
            # marker — never a bare ValueError that escapes grade_trial [GR-6].
            return HoldoutRun({"__malformed__": True}, proc.returncode)
        return HoldoutRun(raw, proc.returncode)


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
    def __init__(self, runner: Optional[GradeRunner] = None, *, image: Optional[str] = None):
        self._runner = runner or DockerGradeRunner()
        self._image = image or os.environ.get("VERDI_GRADER_IMAGE", DEFAULT_GRADER_IMAGE)

    def build_grade_command(self, workspace: Path, holdouts_dir: str) -> list[str]:
        """Fresh, network-less container; holdouts read-only."""
        cmd = ["docker", "run", "--rm", "--network", "none"]
        cmd += ["--volume", f"{Path(workspace).resolve()}:/workspace"]
        if holdouts_dir:
            # holdouts bind-mounted READ-ONLY [AC-1]
            cmd += ["--volume", f"{Path(holdouts_dir).resolve()}:/holdouts:ro"]
        cmd += ["--workdir", "/workspace", self._image]
        return cmd

    def run(self, workspace: Path, holdouts_dir: str) -> HoldoutRun:
        workspace = Path(workspace)
        if getattr(self._runner, "fresh_workspace_copy", False):
            return self._run_on_fresh_copy(workspace, holdouts_dir)
        cmd = self.build_grade_command(workspace, holdouts_dir)
        return self._runner.run_holdouts(cmd, workspace, holdouts_dir)

    def _run_on_fresh_copy(self, workspace: Path, holdouts_dir: str) -> HoldoutRun:
        """Grade a throwaway copy of the workspace.

        The copy protects the ledgered trial evidence from a rw container mount
        [GR-3], and deleting any pre-existing results file in the copy stops an
        agent-written ``holdout_results.json`` from masquerading as grader
        output [GR-1]. The original workspace is never mounted.
        """
        tmp = Path(tempfile.mkdtemp(prefix="verdi-grade-"))
        try:
            copy = tmp / "workspace"
            shutil.copytree(workspace, copy)
            stale = copy / HOLDOUT_RESULTS
            if stale.exists():
                stale.unlink()
            cmd = self.build_grade_command(copy, holdouts_dir)
            return self._runner.run_holdouts(cmd, copy, holdouts_dir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
