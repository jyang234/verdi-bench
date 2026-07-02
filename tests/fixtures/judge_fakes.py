"""Helpers for judge tests: packets, configs, scripted verdict JSON."""

from __future__ import annotations

import json

from harness.judge.packet import ResponseArtifacts, build_packet
from harness.schema.judge_config import JudgeConfig


def make_config(model="google/gemini-1.5-pro-002", orders="both", temperature=0.0):
    return JudgeConfig(model=model, rubric="rubrics/code-task-v1.md", orders=orders,
                       temperature=temperature)


def make_packet(diff_a="diff for response A", diff_b="diff for response B",
                task_prompt="implement the feature", rubric="Judge on correctness."):
    return build_packet(
        ResponseArtifacts(diff=diff_a, holdout_results=[{"id": "h1", "result": "pass"}]),
        ResponseArtifacts(diff=diff_b, holdout_results=[{"id": "h1", "result": "fail"}]),
        task_prompt=task_prompt,
        rubric=rubric,
    )


def verdict_json(winner, *, response=1, kind="diff", with_evidence=True, confidence=0.9):
    """Build a raw judge output JSON string (Response 1/2 framing)."""
    ev = []
    if with_evidence and winner in ("1", "2"):
        ev = [{"kind": kind, "response": int(winner), "hunk": "@@ hunk"}]
    return json.dumps(
        {"winner": winner, "reason": "because", "evidence": ev, "confidence": confidence}
    )
