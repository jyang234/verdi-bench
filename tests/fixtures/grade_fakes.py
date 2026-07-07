"""Fakes for exercising grading without a live Docker daemon."""

from __future__ import annotations

import json
from pathlib import Path

from harness.grade.container import GradeRunner, GradingContainerError, HoldoutRun


class ScriptedGradeRunner(GradeRunner):
    """Returns a scripted holdout output; optionally raises to simulate faults.

    A docker-path stand-in: it grades a fresh copy (``grades_in_place=False``) and
    runs plugins in-process (``runs_plugins_in_container=False``); its non-docker
    ``grader_name`` keeps its grades in the ADVISORY tier, as the getattr default
    ``"unknown"`` did before the runner protocol was formalized [refactor 05 §2]."""

    grader_name = "scripted"
    runs_plugins_in_container = False
    grades_in_place = False

    def __init__(self, output: dict | None = None, *, container_error: bool = False):
        self.output = output
        self.container_error = container_error

    def preflight(self) -> None:
        """No daemon to probe — the scripted path is always available."""

    def run_holdouts(self, cmd, workspace, holdouts_dir, nonce=None) -> HoldoutRun:
        if self.container_error:
            raise GradingContainerError("simulated daemon failure")
        return HoldoutRun(self.output if self.output is not None else {})


class SeqGradeRunner(GradeRunner):
    """Yields a sequence of outputs across k calls (for flake baselines).

    F-L12: exhaustion RAISES instead of silently replaying the last item — a
    replay can hide a miscounted or under-scripted test (the FakeProvider
    RN-18 precedent). A docker-path stand-in like ScriptedGradeRunner."""

    grader_name = "scripted"
    runs_plugins_in_container = False
    grades_in_place = False

    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)
        self.calls = 0

    def preflight(self) -> None:
        """No daemon to probe — the scripted path is always available."""

    def run_holdouts(self, cmd, workspace, holdouts_dir, nonce=None) -> HoldoutRun:
        if self.calls >= len(self.outputs):
            raise AssertionError(
                f"SeqGradeRunner exhausted: call {self.calls + 1} but only "
                f"{len(self.outputs)} output(s) scripted [F-L12]"
            )
        out = self.outputs[self.calls]
        self.calls += 1
        return HoldoutRun(out)


def write_workspace(tmp_path: Path, name: str = "ws") -> Path:
    ws = tmp_path / name
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "solution.txt").write_text("agent output", encoding="utf-8")
    return ws
