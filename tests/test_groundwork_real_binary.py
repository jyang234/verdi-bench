"""Real-binary integration proof for the groundwork grader plugin [plan §3 A1].

Gated on VERDI_FLOWMAP_BIN / VERDI_GROUNDWORK_BIN (skips with a clear reason
otherwise). It drives the FULL real plugin path — resolve assets from the
holdouts side, regenerate the branch graph from the workspace with the pinned
flowmap, run ``groundwork review``, map the verdict — via ``LocalGradeRunner``
(in-process ADVISORY tier, no Docker) on the planted invsvc fixture:

* the violating variant (read route reaches a DB INSERT) → a ``failed`` rule
  assertion (the must_not_reach violation), and
* the reference variant (read-only feature) → a ``passed`` verdict, no fails.

This is the no-Docker sibling of the docker-marked container proof; run it with
the sibling-built binaries:

    VERDI_FLOWMAP_BIN=/path/to/flowmap VERDI_GROUNDWORK_BIN=/path/to/groundwork \
      uv run pytest tests/test_groundwork_real_binary.py -q
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from harness.grade.container import GradingContainer, LocalGradeRunner
from harness.grade.deterministic import grade_trial
from harness.grade.types import AssertionResult, GradeTask
from harness.ledger.events import EventContext
from harness.ledger.query import find_events
from tests.fixtures.groundwork_fixtures import INVSVC_DIR

_BINARIES_SET = bool(os.environ.get("VERDI_FLOWMAP_BIN") and os.environ.get("VERDI_GROUNDWORK_BIN"))

pytestmark = pytest.mark.skipif(
    not _BINARIES_SET,
    reason="set VERDI_FLOWMAP_BIN and VERDI_GROUNDWORK_BIN (sibling-built flowmap/"
    "groundwork) to run the real-binary groundwork integration test",
)

_HOLDOUTS = INVSVC_DIR / "holdouts"


def _grade(tmp_path: Path, variant: str) -> dict:
    """Grade one invsvc variant through the real plugin path; return the grade."""
    workspace = tmp_path / variant
    shutil.copytree(INVSVC_DIR / variant, workspace)
    # LocalGradeRunner reads a pre-placed holdout result (a passing functional
    # holdout); the plugin is what exercises the real toolchain here.
    (workspace / "holdout_results.json").write_text(
        json.dumps({"assertions": [{"id": "h1", "result": "pass"}]}), encoding="utf-8")
    ledger = tmp_path / f"{variant}.ndjson"
    task = GradeTask(
        id="invsvc", task_sha="invsvc-integration",
        holdouts_dir=str(_HOLDOUTS), plugin_ids=["groundwork"],
    )
    out = grade_trial(
        f"trial-{variant}", task, workspace, ledger,
        EventContext(experiment_id="e", clock=lambda: "t"),
        container=GradingContainer(runner=LocalGradeRunner()),
    )
    assert out.graded is True, f"expected a grade, got cant_grade={find_events(ledger, 'cant_grade')}"
    return find_events(ledger, "grade")[0]


def _plugin_assertions(grade: dict) -> list[dict]:
    return [a for a in grade["assertions"] if a["source"] == "plugin:groundwork"]


def test_violating_variant_flags_a_failed_rule(tmp_path):
    grade = _grade(tmp_path, "violating")
    plugin = _plugin_assertions(grade)
    rules = {a["id"]: a["result"] for a in plugin}
    assert rules.get("must_not_reach") == AssertionResult.failed.value, plugin
    # the top-line verdict is BLOCK → failed
    assert rules.get("groundwork:verdict") == AssertionResult.failed.value, plugin


def test_reference_variant_passes_with_no_failed_rule(tmp_path):
    grade = _grade(tmp_path, "reference")
    plugin = _plugin_assertions(grade)
    results = {a["result"] for a in plugin}
    assert AssertionResult.failed.value not in results, plugin
    # STRUCTURALLY-CLEAR → the top-line verdict passes
    assert any(
        a["id"] == "groundwork:verdict" and a["result"] == AssertionResult.passed.value
        for a in plugin
    ), plugin


def test_blindspot_variant_abstains_without_a_failed_rule(tmp_path):
    """The blind-spot fixture: a must_not_reach rule the reflect frontier makes
    unprovable → abstain, never a fail. Binary score is unaffected (holdout-only)."""
    workspace = tmp_path / "blindspot"
    shutil.copytree(INVSVC_DIR / "blindspot", workspace)
    (workspace / "holdout_results.json").write_text(
        json.dumps({"assertions": [{"id": "h1", "result": "pass"}]}), encoding="utf-8")
    ledger = tmp_path / "blindspot.ndjson"
    task = GradeTask(
        id="alertsvc", task_sha="alertsvc-integration",
        holdouts_dir=str(INVSVC_DIR / "blindspot" / "holdouts"),
        plugin_ids=["groundwork"],
    )
    grade_trial(
        "trial-blindspot", task, workspace, ledger,
        EventContext(experiment_id="e", clock=lambda: "t"),
        container=GradingContainer(runner=LocalGradeRunner()),
    )
    grade = find_events(ledger, "grade")[0]
    plugin = _plugin_assertions(grade)
    results = {a["result"] for a in plugin}
    assert AssertionResult.abstain.value in results, plugin
    assert AssertionResult.failed.value not in results, plugin
    assert grade["binary_score"] is True  # holdout passed; plugin abstain irrelevant
