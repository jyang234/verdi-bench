"""Retrospective score decomposition — pure-core tests (no docker, no ledgers).

Pins the two pure seams of scripts/flagship/decompose_scores.py: the fused
holdout-script split (functional half / gate half) against the REAL corpus
builder's argv shape, and the ledger walk that joins trial and grade events.
The docker re-execution itself is validated operationally: the script recomputes
fused = functional AND gate and refuses (nonzero exit) on any mismatch with the
recorded binary_score.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts" / "flagship"))
sys.path.insert(0, str(_REPO / "corpora" / "groundwork-v0"))

import build_tasks  # noqa: E402  (stdlib-only; import needs no binaries)
import decompose_scores as ds  # noqa: E402


def test_split_round_trips_the_real_builder_argv():
    argv = build_tasks.holdout_argv(
        "gw-r5", [("internal/wire/feature_test.go", "package wire\n")]
    )
    functional, gate = ds.split_holdout_argv(argv)
    assert functional[:2] == ["sh", "-c"] and gate[:2] == ["sh", "-c"]
    assert functional[2].endswith("go test ./...")
    assert "verdi-groundwork-check gw-r5" in gate[2]
    assert "verdi-groundwork-check" not in functional[2]
    assert "go test" not in gate[2]
    # the functional half keeps the holdouts-root binding + the cp injection
    assert 'H="${VERDI_HOLDOUTS_DIR:-/holdouts}"' in functional[2]
    assert "cp " in functional[2]
    # the gate half stays fail-fast
    assert gate[2].startswith("set -e; ")


def test_split_refuses_unexpected_shapes():
    with pytest.raises(ValueError, match="argv shape"):
        ds.split_holdout_argv(["bash", "-c", "true"])
    with pytest.raises(ValueError, match="holdout script"):
        ds.split_holdout_argv(["sh", "-c", "echo hi; true"])


def test_load_graded_trials_joins_trial_and_grade(tmp_path):
    ledger = tmp_path / "ledger.ndjson"
    rows = [
        {"event": "experiment_locked", "spec_sha256": "x"},
        {"event": "trial", "trial_record": {
            "trial_id": "trial-a", "task_id": "gw-r5", "arm": "haiku-bare",
            "artifacts_path": "runs/x/workspaces/trial-a/artifacts"}},
        {"event": "trial", "trial_record": {
            "trial_id": "trial-ungraded", "task_id": "gw-r5", "arm": "haiku-bare",
            "artifacts_path": "runs/x/workspaces/trial-ungraded/artifacts"}},
        {"event": "grade", "trial_id": "trial-a", "binary_score": False,
         "assertions": [
             {"id": "gw-r5-functional-groundwork", "source": "holdout_test",
              "result": "fail", "detail": "verdi-groundwork-check: BLOCK"},
             {"id": "groundwork:verdict", "source": "plugin:groundwork",
              "result": "fail", "detail": "groundwork review verdict: BLOCK"},
         ]},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    trials = ds.load_graded_trials(ledger)
    assert len(trials) == 1  # ungraded trials are excluded, not guessed at
    t = trials[0]
    assert t["trial_id"] == "trial-a"
    assert t["task_id"] == "gw-r5"
    assert t["arm"] == "haiku-bare"
    assert t["workspace"] == "runs/x/workspaces/trial-a"
    assert t["binary_score"] is False
    assert t["advisory_verdict"] == "groundwork review verdict: BLOCK"


def test_load_graded_trials_refuses_reused_trial_events(tmp_path):
    # A ledger built with control-reuse carries `reused_trial` / `reused_grade`
    # events (a DISTINCT kind from `trial`/`grade`). Consuming only the native
    # kinds would SILENTLY subset the reused cells out of the decomposition.
    # The script refuses loudly rather than mis-decompose a partial ledger.
    ledger = tmp_path / "ledger.ndjson"
    rows = [
        {"event": "experiment_locked", "spec_sha256": "x"},
        {"event": "reused_trial", "trial_record": {
            "trial_id": "trial-r", "task_id": "gw-r5", "arm": "haiku-bare",
            "artifacts_path": "runs/x/workspaces/trial-r/artifacts"},
         "reused_from": {"source_experiment_id": "prior", "bundle_sha256": "y"}},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="reused_trial"):
        ds.load_graded_trials(ledger)


def test_load_graded_trials_refuses_reused_grade_events(tmp_path):
    ledger = tmp_path / "ledger.ndjson"
    rows = [
        {"event": "reused_grade", "grade": {"trial_id": "trial-r"},
         "reused_from": {"source_experiment_id": "prior", "bundle_sha256": "y"}},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="reused_grade"):
        ds.load_graded_trials(ledger)


def _decomposed_row(**overrides) -> dict:
    """A synthetic row with exactly the keys ``decompose_experiment`` produces."""
    row = {
        "trial_id": "trial-a", "task_id": "gw-r5", "arm": "haiku-bare",
        "workspace": "runs/x/workspaces/trial-a", "binary_score": True,
        "advisory_verdict": None, "experiment": "recon",
        "functional_pass": True, "gate_pass": True, "fused_recomputed": True,
        "fused_matches_recorded": True, "functional_detail": None,
        "gate_detail": None,
    }
    row.update(overrides)
    return row


def test_render_markdown_lists_mismatches_with_count():
    clean = _decomposed_row(trial_id="trial-a")
    # A row whose recomputed fused disagrees with the recorded binary_score:
    # gate flipped, so fused_recomputed False but binary_score recorded True.
    mismatch = _decomposed_row(
        trial_id="trial-b", gate_pass=False, fused_recomputed=False,
        fused_matches_recorded=False, binary_score=True,
        gate_detail="verdi-groundwork-check: BLOCK",
    )
    out = ds.render_markdown([clean, mismatch])
    assert "fused-vs-recorded mismatches: 1" in out
    assert "MISMATCH recon/trial-b" in out
    assert "recomputed False vs recorded True" in out
    # the agreeing row is not listed as a mismatch
    assert "MISMATCH recon/trial-a" not in out
