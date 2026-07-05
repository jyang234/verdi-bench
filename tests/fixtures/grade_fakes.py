"""Fakes for exercising grading without a live Docker daemon."""

from __future__ import annotations

import json
from pathlib import Path

from harness.grade.container import GradingContainerError, HoldoutRun


class ScriptedGradeRunner:
    """Returns a scripted holdout output; optionally raises to simulate faults."""

    def __init__(self, output: dict | None = None, *, container_error: bool = False):
        self.output = output
        self.container_error = container_error

    def run_holdouts(self, cmd, workspace, holdouts_dir) -> HoldoutRun:
        if self.container_error:
            raise GradingContainerError("simulated daemon failure")
        return HoldoutRun(self.output if self.output is not None else {})


class SeqGradeRunner:
    """Yields a sequence of outputs across k calls (for flake baselines).

    F-L12: exhaustion RAISES instead of silently replaying the last item — a
    replay can hide a miscounted or under-scripted test (the FakeProvider
    RN-18 precedent)."""

    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)
        self.calls = 0

    def run_holdouts(self, cmd, workspace, holdouts_dir) -> HoldoutRun:
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
