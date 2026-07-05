"""Exploratory analyze over a reused control [control-reuse plan, slice 6].

A reused control produces an UNPAIRED exploratory section (computed estimate +
judge win-rate) that never backs an official decision, and the official paired
path is byte-untouched (findings.reuse is None on a non-reuse ledger).
"""

from __future__ import annotations

import yaml

from harness.analyze.report import compute_findings, render_markdown
from harness.blind.core import arm_canaries
from harness.corpus.commit import load_task_dicts
from harness.judge.reuse import judge_reused
from harness.run.reuse import build_bundle, import_bundle
from harness.run.settings import load_run_settings
from tests.fixtures.builders import (
    fixed_ctx,
    locked_experiment,
    seed_trial_and_grade,
)

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


def _reuse_target(tmp_path):
    """A fully wired reuse experiment: imported control + fresh contender +
    reused judge verdicts."""
    src = tmp_path / "src-exp"
    locked_experiment(src, judge=_FAKE_JUDGE)
    _lay_tasks(src)
    for tid in ("t1", "t2"):
        seed_trial_and_grade(src / "ledger.ndjson", fixed_ctx("src-exp"),
                             trial_id=f"ctrl-{tid}", task_id=tid, arm="control", passed=True)
    bundle = build_bundle(src, "control")

    tgt = tmp_path / "tgt-exp"
    spec, _sp, ledger = locked_experiment(tgt, judge=_FAKE_JUDGE)
    _lay_tasks(tgt)
    settings = load_run_settings(tgt, spec=spec)
    import_bundle(tgt, bundle, fixed_ctx("tgt-exp"), engine="fake", spec=spec, settings=settings)
    for tid in ("t1", "t2"):
        seed_trial_and_grade(ledger, fixed_ctx("tgt-exp"),
                             trial_id=f"cont-{tid}", task_id=tid, arm="treatment", passed=False)
    task_dicts = load_task_dicts(tgt)
    judge_reused(
        ledger, tgt, spec, fixed_ctx("tgt-exp"),
        rubric=(tgt / spec.judge.rubric).read_text(encoding="utf-8"),
        prompts={t["id"]: t["prompt"] for t in task_dicts},
        canaries=arm_canaries(spec.arms),
        task_classes={t["id"]: "default" for t in task_dicts},
    )
    return tgt, spec, ledger


def test_reuse_section_is_computed_and_unpaired(tmp_path):
    tgt, spec, ledger = _reuse_target(tmp_path)
    findings = compute_findings(ledger, spec, spec.seed, n_boot=200)
    r = findings.reuse
    assert r is not None
    assert r["control_arm"] == "control" and r["contender_arm"] == "treatment"
    assert r["official_decision"] is False
    # control passed its holdouts (1.0), contender failed (0.0) — unpaired
    assert r["computed"]["control_mean"] == 1.0
    assert r["computed"]["contender_mean"] == 0.0
    assert r["computed"]["delta_contender_minus_control"] == -1.0
    assert r["computed"]["paired"] is False
    # control (A) won every reused verdict → contender win-rate 0
    assert r["judge_preference"]["contender_win_rate"] == 0.0


def test_reuse_never_backs_an_official_decision(tmp_path):
    tgt, spec, ledger = _reuse_target(tmp_path)
    findings = compute_findings(ledger, spec, spec.seed, n_boot=200)
    # the official paired comparison has no fresh control to pair against
    assert all(c.excluded_from_official for c in findings.comparisons)
    # reuse rides the exploratory render, labelled; and is disclosed in official
    exploratory = render_markdown(findings, ledger, "exploratory")
    assert "Control reuse (EXPLORATORY, unpaired)" in exploratory
    assert "never an official decision" in exploratory


def test_no_reuse_leaves_section_none(tmp_path):
    tgt = tmp_path / "plain"
    spec, _sp, ledger = locked_experiment(tgt, judge=_FAKE_JUDGE)
    _lay_tasks(tgt)
    for tid in ("t1", "t2"):
        for arm in ("control", "treatment"):
            seed_trial_and_grade(ledger, fixed_ctx("plain"),
                                 trial_id=f"{arm}-{tid}", task_id=tid, arm=arm, passed=True)
    findings = compute_findings(ledger, spec, spec.seed, n_boot=200)
    assert findings.reuse is None  # official output byte-identical on non-reuse ledgers
