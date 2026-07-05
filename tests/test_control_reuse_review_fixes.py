"""Regression tests for control-reuse code-review fixes.

Each test fails if the corresponding defect returns.
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from harness.analyze.report import _reuse_judge_winrate
from harness.cli import app
from harness.corpus.commit import TaskCommitmentError, holdout_content_sha
from harness.ledger import events
from harness.ledger.events import EventContext
from harness.ledger.query import find_events
from harness.run.control_reuse import ControlReuseError, primary_pair_contender
from harness.run.reuse import (
    ControlBundleError,
    build_bundle,
    bundle_sha,
    import_bundle,
    reused_arms,
    reused_diff_path,
)
from harness.run.settings import load_run_settings
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

runner = CliRunner()

_TASKS = {"tasks": [
    {"id": "t1", "prompt": "p1", "holdouts_dir": "holdouts/t1"},
    {"id": "t2", "prompt": "p2", "holdouts_dir": "holdouts/t2"},
]}
_FAKE_JUDGE = {
    "model": "fake/deterministic-2026-01-01", "rubric": "rubrics/code-task-v1.md",
    "orders": "both", "temperature": 0,
}


def _lay_tasks(exp_dir):
    (exp_dir / "tasks.yaml").write_text(yaml.safe_dump(_TASKS), encoding="utf-8")
    for tid, body in (("t1", "assert a"), ("t2", "assert b")):
        d = exp_dir / "holdouts" / tid
        d.mkdir(parents=True)
        (d / "holdout.json").write_text(body, encoding="utf-8")


def _source_bundle(tmp_path):
    src = tmp_path / "src"
    locked_experiment(src, judge=_FAKE_JUDGE)
    _lay_tasks(src)
    for tid in ("t1", "t2"):
        seed_trial_and_grade(src / "ledger.ndjson", fixed_ctx("src"),
                             trial_id=f"ctrl-{tid}", task_id=tid, arm="control", passed=True)
    return build_bundle(src, "control")


def _import_target(tmp_path, bundle, *, with_contender=True):
    tgt = tmp_path / "tgt"
    spec, _sp, ledger = locked_experiment(tgt, judge=_FAKE_JUDGE)
    _lay_tasks(tgt)
    settings = load_run_settings(tgt, spec=spec)
    import_bundle(tgt, bundle, fixed_ctx("tgt"), engine="fake", spec=spec, settings=settings)
    if with_contender:  # fresh contender trials so reuse comparisons can pair
        for tid in ("t1", "t2"):
            seed_trial_and_grade(ledger, fixed_ctx("tgt"),
                                 trial_id=f"cont-{tid}", task_id=tid, arm="treatment", passed=False)
    return tgt, spec, ledger, settings


# --- C#2: resume without --reuse-control must not run the control fresh ------
def test_reused_arms_reads_from_ledger(tmp_path):
    bundle = _source_bundle(tmp_path)
    tgt, spec, ledger, _ = _import_target(tmp_path, bundle)
    assert reused_arms(ledger) == {"control"}


def test_cli_resume_without_flag_does_not_run_control_fresh(tmp_path):
    # full-pipeline: source run+export, target run --reuse-control, then resume
    # WITHOUT the flag — the control arm must never execute natively.
    src = tmp_path / "src"
    src.mkdir()
    from tests.fixtures.builders import write_experiment_yaml
    write_experiment_yaml(src / "experiment.yaml")
    tasks = [{"id": "t1", "prompt": "solve", "fake_behavior": {"native_log": {"total_cost_usd": 0.01}}}]
    (src / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    assert runner.invoke(app, ["plan", str(src / "experiment.yaml"), "--ledger", str(src / "ledger.ndjson")]).exit_code == 0
    assert runner.invoke(app, ["run", str(src)]).exit_code == 0
    bundle_path = tmp_path / "c.json"
    assert runner.invoke(app, ["control-cache", "export", str(src), "--arm", "control", "--out", str(bundle_path)]).exit_code == 0

    tgt = tmp_path / "tgt"
    tgt.mkdir()
    write_experiment_yaml(tgt / "experiment.yaml")
    (tgt / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    tgt_ledger = tgt / "ledger.ndjson"
    assert runner.invoke(app, ["plan", str(tgt / "experiment.yaml"), "--ledger", str(tgt_ledger)]).exit_code == 0
    assert runner.invoke(app, ["run", str(tgt), "--reuse-control", str(bundle_path)]).exit_code == 0
    # resume WITHOUT the flag
    r = runner.invoke(app, ["run", str(tgt)])
    assert r.exit_code == 0, r.output
    native_arms = {ev["trial_record"]["arm"] for ev in find_events(tgt_ledger, "trial")}
    assert native_arms == {"treatment"}, "the reused control arm ran fresh on resume"


# --- G#2: resume after gated drift is idempotent (already_imported first) ----
def test_resume_after_holdout_drift_does_not_re_gate(tmp_path):
    bundle = _source_bundle(tmp_path)
    tgt, spec, ledger, settings = _import_target(tmp_path, bundle)
    # a drift that WOULD fail the fingerprint gate on a fresh import
    (tgt / "holdouts" / "t1" / "holdout.json").write_text("DRIFTED", encoding="utf-8")
    # but the control is already fully imported — resume must not re-gate/refuse
    import_bundle(tgt, bundle, fixed_ctx("tgt"), engine="fake", spec=spec, settings=settings)
    assert len(find_events(ledger, events.CONTROL_REUSED)) == 1


# --- B#3: partial import completes on resume; control_reused is the marker ----
def test_partial_import_resumes_without_duplication(tmp_path):
    bundle = _source_bundle(tmp_path)
    tgt = tmp_path / "tgt"
    spec, _sp, ledger = locked_experiment(tgt, judge=_FAKE_JUDGE)
    _lay_tasks(tgt)
    settings = load_run_settings(tgt, spec=spec)
    # simulate a crash mid-import: cell ctrl-t1 written, control_reused NOT yet
    events.record_reused_trial(
        ledger, fixed_ctx("tgt"),
        trial_record={"trial_id": "ctrl-t1", "task_id": "t1", "arm": "control", "repetition": 0},
        reused_from={"source_experiment_id": "src", "bundle_sha256": bundle["bundle_sha256"]},
    )
    import_bundle(tgt, bundle, fixed_ctx("tgt"), engine="fake", spec=spec, settings=settings)
    # ctrl-t1 not duplicated; ctrl-t2 added; completion marker appended last
    assert len(find_events(ledger, events.REUSED_TRIAL)) == 2
    assert len(find_events(ledger, events.CONTROL_REUSED)) == 1


# --- H#3: tampered / missing diff snapshot fails loudly ----------------------
def test_tampered_diff_snapshot_refuses(tmp_path):
    from harness.judge.reuse import comparisons_from_reuse

    bundle = _source_bundle(tmp_path)
    tgt, spec, ledger, _ = _import_target(tmp_path, bundle)
    # tamper the on-disk snapshot beside the ledger (outside the hash chain)
    reused_diff_path(tgt, "ctrl-t1").write_text("TAMPERED BYTES", encoding="utf-8")
    with pytest.raises(ControlReuseError, match=r"does not match its recorded diff_sha256"):
        comparisons_from_reuse(ledger, tgt, spec)


def test_missing_diff_snapshot_refuses(tmp_path):
    from harness.judge.reuse import comparisons_from_reuse

    bundle = _source_bundle(tmp_path)
    tgt, spec, ledger, _ = _import_target(tmp_path, bundle)
    reused_diff_path(tgt, "ctrl-t1").unlink()
    with pytest.raises(ControlReuseError, match=r"snapshot missing"):
        comparisons_from_reuse(ledger, tgt, spec)


# --- G#1: >2-arm / non-primary-pair control refused loudly at import ---------
def test_primary_pair_contender_helper(tmp_path):
    spec, _sp, _l = locked_experiment(tmp_path / "e")
    assert primary_pair_contender(spec, "control") == "treatment"
    assert primary_pair_contender(spec, "treatment") == "control"
    assert primary_pair_contender(spec, "ghost") is None


def test_import_refuses_control_not_in_primary_pair(tmp_path):
    tgt = tmp_path / "tgt"
    spec, _sp, ledger = locked_experiment(tgt, judge=_FAKE_JUDGE)
    _lay_tasks(tgt)
    settings = load_run_settings(tgt, spec=spec)
    crafted = {
        "bundle_version": 1, "source_experiment_id": "src", "source_ledger_head_hash": "h",
        "control_arm": "ghost", "fingerprint": {}, "audit": {}, "cells": [],
    }
    crafted["bundle_sha256"] = bundle_sha(crafted)
    with pytest.raises(ControlBundleError, match=r"not in the pre-registered primary pair"):
        import_bundle(tgt, crafted, fixed_ctx("tgt"), engine="fake", spec=spec, settings=settings)


# --- A#4: non-dir holdouts_dir fails loudly ----------------------------------
def test_holdout_content_sha_refuses_non_directory(tmp_path):
    f = tmp_path / "holdouts.json"
    f.write_text("not a dir", encoding="utf-8")
    with pytest.raises(TaskCommitmentError, match=r"not a directory"):
        holdout_content_sha(f)


# --- A#5: unmapped/foreign judge verdict excluded from win-rate denominator --
def test_winrate_excludes_unmapped_verdict(tmp_path):
    ledger = tmp_path / "ledger.ndjson"
    ctx = EventContext(experiment_id="e", actor="t", clock=lambda: "2026-01-01T00:00:00+00:00")
    rf = {"source_experiment_id": "s", "bundle_sha256": "b"}
    # one properly-mapped contender win, one decided verdict with NO arm_map
    events.append_reused_verdict(ledger, ctx, verdict={
        "winner": "B", "arm_map": {"A": "control", "B": "treatment"}, "comparison_id": "c1"}, reused_from=rf)
    events.append_reused_verdict(ledger, ctx, verdict={
        "winner": "A", "comparison_id": "c2"}, reused_from=rf)  # unmapped
    wr = _reuse_judge_winrate(ledger, "treatment", "control")
    assert wr["decided"] == 1  # the unmapped verdict is excluded, not counted
    assert wr["contender_win_rate"] == 1.0


# --- B#1: judge_reused honors the token ceiling ------------------------------
def test_judge_reused_stops_at_token_ceiling(tmp_path):
    from harness.judge.reuse import judge_reused

    bundle = _source_bundle(tmp_path)
    tgt, spec, ledger, _ = _import_target(tmp_path, bundle)
    from harness.corpus.commit import load_task_dicts
    from harness.blind.core import arm_canaries
    task_dicts = load_task_dicts(tgt)
    n = judge_reused(
        ledger, tgt, spec, fixed_ctx("tgt"),
        rubric=(tgt / spec.judge.rubric).read_text(encoding="utf-8"),
        prompts={t["id"]: t["prompt"] for t in task_dicts},
        canaries=arm_canaries(spec.arms),
        task_classes={t["id"]: "default" for t in task_dicts},
        ceiling=100, accumulated=100,  # already at the cap
    )
    assert n == 0  # refuse-to-start at the ceiling
    assert find_events(ledger, events.REUSED_JUDGE_VERDICT) == []
    assert len(find_events(ledger, events.JUDGE_STOPPED_TOKEN_CEILING)) == 1
