"""Workspace evidence commitment [F-H3] — the trajectory-sha seam's twin.

Grading commits a canonical hash of the workspace's solution bytes onto the
grade event; the forensic and contamination scanners verify it before trusting
live disk (their tests live beside those scanners). This file owns the
canonical walk and the resolver's closed status vocabulary.
"""

from __future__ import annotations

import os

from harness.grade.container import GradingContainer
from harness.grade.deterministic import grade_trial
from harness.grade.types import GradeTask
from harness.run.workspace import (
    ABSENT,
    MISSING_WORKSPACE,
    SHA_MISMATCH,
    VERIFIED,
    WORKSPACE_WALK_VERSION,
    resolve_workspace,
    workspace_sha256,
)
from tests.fixtures.builders import ctx_for
from tests.fixtures.grade_fakes import ScriptedGradeRunner

_PASS = {"assertions": [{"id": "h1", "result": "pass"}]}


def _ws(tmp_path, name="ws"):
    ws = tmp_path / name
    (ws / "artifacts").mkdir(parents=True)
    (ws / "pkg").mkdir()
    (ws / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "data.bin").write_bytes(b"\x00\xff\x00")
    (ws / "artifacts" / "transcript.txt").write_text("log", encoding="utf-8")
    (ws / "holdout_results.json").write_text("{}", encoding="utf-8")
    return ws


def test_hash_is_deterministic_and_creation_order_independent(tmp_path):
    a = _ws(tmp_path, "a")
    b = tmp_path / "b"
    (b / "artifacts").mkdir(parents=True)
    (b / "data.bin").write_bytes(b"\x00\xff\x00")  # reversed creation order
    (b / "pkg").mkdir()
    (b / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    assert workspace_sha256(a) == workspace_sha256(a)
    assert workspace_sha256(a) == workspace_sha256(b)


def test_hash_covers_content_but_not_excluded_trees(tmp_path):
    ws = _ws(tmp_path)
    base = workspace_sha256(ws)
    # artifacts/, the grader output, and symlinks are outside the commitment
    (ws / "artifacts" / "transcript.txt").write_text("edited log", encoding="utf-8")
    (ws / "holdout_results.json").write_text('{"forged": true}', encoding="utf-8")
    os.symlink("/etc/hostname", ws / "link")
    assert workspace_sha256(ws) == base
    # solution bytes — including binary — are inside it
    (ws / "data.bin").write_bytes(b"\x00\xff\x01")
    assert workspace_sha256(ws) != base


def test_resolve_workspace_closed_vocabulary(tmp_path):
    ws = _ws(tmp_path)
    sha = workspace_sha256(ws)
    assert resolve_workspace(ws, None) == ABSENT
    assert resolve_workspace(tmp_path / "gone", sha) == MISSING_WORKSPACE
    assert resolve_workspace(ws, sha) == VERIFIED
    (ws / "pkg" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    assert resolve_workspace(ws, sha) == SHA_MISMATCH


def test_h3_grade_event_carries_the_workspace_commitment(tmp_path):
    """F-H3: grading is the moment the workspace becomes evidence — the grade
    event commits its canonical hash so scanners can verify what they read."""
    ws = _ws(tmp_path)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="t1", task_sha="sha-abc")
    container = GradingContainer(runner=ScriptedGradeRunner(_PASS))
    outcome = grade_trial("trial-1", task, ws, ledger, ctx_for(tmp_path), container=container)
    assert outcome.graded
    assert outcome.event["workspace_sha256"] == workspace_sha256(ws)
    assert outcome.event["workspace_walk_version"] == WORKSPACE_WALK_VERSION
