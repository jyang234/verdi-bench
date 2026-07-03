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
    """The grader ran but failed (nonzero exit, no results) → a **terminal**
    cant_grade(container_failure): re-running won't change the outcome."""


class GraderUnavailableError(GradingContainerError):
    """The grader could not be run at all — daemon/config/OS error or exit 125 →
    a **transient** cant_grade(grader_unavailable) that a later attempt may
    resolve [GR-11]. Subclass of GradingContainerError so callers that don't care
    still catch it."""


@dataclass
class HoldoutRun:
    raw_output: dict
    exit_status: int = 0


class GradeRunner(Protocol):
    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun: ...


class DockerGradeRunner:
    """Runs holdouts in a fresh network-less container via the docker CLI.

    Uses :class:`GradingContainer`'s default (safe) path: a throwaway copy of the
    trial workspace with any pre-existing results file removed, so the container
    produces its own output and cannot mutate ledgered evidence [GR-1/GR-3].
    """

    grader_name = "docker"

    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.SubprocessError) as e:
            # Could not run the grader at all — transient infra failure [GR-11].
            raise GraderUnavailableError(str(e)) from e
        # exit 125 is a docker daemon/config error (grader never ran) → transient.
        if proc.returncode == 125:
            raise GraderUnavailableError("docker daemon/config error (exit 125)")
        # Any other nonzero exit means the grader RAN and failed — not that holdout
        # tests failed (those are per-assertion in the results file at exit 0).
        # Terminal: refuse rather than scoring a stale/partial workspace file, and
        # do not retry a deterministic failure forever [GR-2/GR-11].
        if proc.returncode != 0:
            detail = proc.stderr.strip()
            raise GradingContainerError(
                f"grader container exited {proc.returncode}"
                + (f": {detail}" if detail else "")
            )
        results = Path(workspace) / HOLDOUT_RESULTS
        if not results.exists():
            raise GradingContainerError("grader produced no holdout_results.json")
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
    Docker (the real DockerGradeRunner is docker-marked).

    ``grades_in_place`` opts out of the fresh-copy isolation: it must read the
    pre-placed file in the original workspace. It is a read-only path (it never
    mounts or writes the workspace), so it does not mutate evidence. Because it
    scores a file the harness/agent placed rather than a container's own output,
    its grades are stamped ``grader_name = "local"`` (ADVISORY) so they are
    distinguishable from a trusted container grade.
    """

    grades_in_place = True
    grader_name = "local"

    def run_holdouts(self, cmd: list[str], workspace: Path, holdouts_dir: str) -> HoldoutRun:
        results = Path(workspace) / HOLDOUT_RESULTS
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

    @property
    def grader_name(self) -> str:
        """Identity of the grader used, recorded in the grade event so a local
        (ADVISORY) grade is distinguishable from a trusted container grade [SEC]."""
        return getattr(self._runner, "grader_name", "unknown")

    def run(self, workspace: Path, holdouts_dir: str) -> HoldoutRun:
        workspace = Path(workspace)
        # Fail SAFE: grade a throwaway copy by DEFAULT. Only a runner that must
        # read a pre-placed file in the original workspace (the no-daemon
        # LocalGradeRunner) opts out via ``grades_in_place`` — a future container
        # runner therefore cannot silently lose evidence protection [GR-1/GR-3].
        if getattr(self._runner, "grades_in_place", False):
            cmd = self.build_grade_command(workspace, holdouts_dir)
            return self._runner.run_holdouts(cmd, workspace, holdouts_dir)
        return self._run_on_fresh_copy(workspace, holdouts_dir)

    def _run_on_fresh_copy(self, workspace: Path, holdouts_dir: str) -> HoldoutRun:
        """Grade a throwaway copy of the workspace.

        The copy protects the ledgered trial evidence from a rw container mount
        [GR-3], and deleting any pre-existing results file in the copy stops an
        agent-written ``holdout_results.json`` from masquerading as grader
        output [GR-1]. The original workspace is never mounted.

        The workspace is agent-controlled, so preparation is hardened: symlinks
        are copied as links (never followed — no escape/disk-exhaustion), and a
        results entry of any type (file/dir/symlink) is removed. Any preparation
        error becomes a GradingContainerError so a hostile workspace fails *this*
        trial closed rather than aborting the whole grade batch [SEC].
        """
        tmp = Path(tempfile.mkdtemp(prefix="verdi-grade-"))
        try:
            copy = tmp / "workspace"
            try:
                shutil.copytree(workspace, copy, symlinks=True)
                stale = copy / HOLDOUT_RESULTS
                if stale.is_symlink() or stale.is_file():
                    stale.unlink()
                elif stale.is_dir():
                    shutil.rmtree(stale)
            except OSError as e:
                raise GradingContainerError(
                    f"could not prepare a clean workspace copy: {e}"
                ) from e
            cmd = self.build_grade_command(copy, holdouts_dir)
            return self._runner.run_holdouts(cmd, copy, holdouts_dir)
        finally:
            # Best-effort cleanup of the throwaway copy: a cleanup error must not
            # clobber an already-computed grade [determinism/fail-loudly intent].
            shutil.rmtree(tmp, ignore_errors=True)
