"""Judge a reused control vs a fresh contender [control-reuse plan, slice 5].

comparisons_from_reuse pairs the fresh contender against the reused control
(control diff from the snapshot, holdouts from each side's grade); judge_reused
records reused_judge_verdict — never the native judge_verdict the official path
reads — and is idempotent.
"""

from __future__ import annotations

import yaml

from harness.blind.core import arm_canaries
from harness.corpus.commit import load_task_dicts
from harness.judge.reuse import comparisons_from_reuse, judge_reused
from harness.ledger import events
from harness.ledger.query import find_events
from harness.run.reuse import build_bundle, import_bundle
from harness.run.settings import load_run_settings
from tests.fixtures.builders import ctx_for, fixed_ctx, locked_experiment, seed_trial_and_grade

_TASKS = {"tasks": [
    {"id": "t1", "prompt": "p1", "holdouts_dir": "holdouts/t1"},
    {"id": "t2", "prompt": "p2", "holdouts_dir": "holdouts/t2"},
]}
_FAKE_JUDGE = {
    "model": "fake/deterministic-2026-01-01", "rubric": "rubrics/code-task-v1.md",
    "orders": "both", "temperature": 0,
}
# Arms pinned EXPLICITLY control-first: the reuse comparisons' A/B frame follows
# spec arm order, and this suite's premise ("reused control is A, the fresh
# contender is B") froze under that order — indifferent to which arm the starter
# template now declares first [ux-friction AC-7]; explicit inputs over invisible
# defaults (the D5 principle).
_ARMS_CONTROL_FIRST = [
    {"name": "control", "platform": "claude_code",
     "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
    {"name": "treatment", "platform": "codex",
     "model": "openai/gpt-4o-2024-08-06", "payload": {}},
]


def _lay_tasks(exp_dir):
    (exp_dir / "tasks.yaml").write_text(yaml.safe_dump(_TASKS), encoding="utf-8")
    for tid, body in (("t1", "assert a"), ("t2", "assert b")):
        d = exp_dir / "holdouts" / tid
        d.mkdir(parents=True)
        (d / "holdout.json").write_text(body, encoding="utf-8")


def _source(tmp_path):
    src = tmp_path / "src-exp"
    locked_experiment(src, judge=_FAKE_JUDGE, arms=list(_ARMS_CONTROL_FIRST))
    _lay_tasks(src)
    ledger = src / "ledger.ndjson"
    ctx = fixed_ctx(experiment_id="src-exp")
    for tid in ("t1", "t2"):  # control passes its holdouts
        seed_trial_and_grade(ledger, ctx, trial_id=f"ctrl-{tid}", task_id=tid, arm="control", passed=True)
    return src


def _target_with_reuse(tmp_path):
    """Import a control bundle into a fresh target, then seed the contender."""
    bundle = build_bundle(_source(tmp_path), "control")
    tgt = tmp_path / "tgt-exp"
    spec, _sp, ledger = locked_experiment(
        tgt, judge=_FAKE_JUDGE, arms=list(_ARMS_CONTROL_FIRST)
    )
    _lay_tasks(tgt)
    settings = load_run_settings(tgt, spec=spec)
    import_bundle(tgt, bundle, fixed_ctx(experiment_id="tgt-exp"),
                  engine="fake", spec=spec, settings=settings)
    # fresh contender ("treatment") fails its holdouts, so the judge is decisive
    ctx = fixed_ctx(experiment_id="tgt-exp")
    for tid in ("t1", "t2"):
        seed_trial_and_grade(ledger, ctx, trial_id=f"cont-{tid}", task_id=tid, arm="treatment", passed=False)
    return tgt, spec, ledger


def _judge_inputs(exp_dir, spec):
    task_dicts = load_task_dicts(exp_dir)
    return {
        "rubric": (exp_dir / spec.judge.rubric).read_text(encoding="utf-8"),
        "prompts": {t["id"]: t.get("prompt", "") for t in task_dicts},
        "canaries": arm_canaries(spec.arms),
        "task_classes": {t["id"]: t.get("task_class", "default") for t in task_dicts},
    }


def test_comparisons_pair_contender_against_reused_control(tmp_path):
    tgt, spec, ledger = _target_with_reuse(tmp_path)
    comparisons = comparisons_from_reuse(ledger, tgt, spec)
    assert [c.task_id for c in comparisons] == ["t1", "t2"]
    c = comparisons[0]
    assert c.arm_map == {"A": "control", "B": "treatment"}
    # control (A) passed its holdout; contender (B) failed
    assert [h["result"] for h in c.response_a.holdout_results] == ["pass"]
    assert [h["result"] for h in c.response_b.holdout_results] == ["fail"]


def test_judge_reused_records_reused_verdicts_only(tmp_path):
    tgt, spec, ledger = _target_with_reuse(tmp_path)
    ctx = fixed_ctx(experiment_id="tgt-exp")
    n = judge_reused(ledger, tgt, spec, ctx, **_judge_inputs(tgt, spec))
    assert n == 2
    # the exploratory kind is populated; the official kind is untouched
    reused = find_events(ledger, events.REUSED_JUDGE_VERDICT)
    assert len(reused) == 2
    assert find_events(ledger, events.JUDGE_VERDICT) == []
    # control (A) won on holdouts, and the verdict carries reuse provenance
    assert reused[0]["verdict"]["winner"] == "A"
    assert reused[0]["reused_from"]["source_experiment_id"] == "src-exp"


def test_judge_reused_is_idempotent(tmp_path):
    tgt, spec, ledger = _target_with_reuse(tmp_path)
    ctx = fixed_ctx(experiment_id="tgt-exp")
    judge_reused(ledger, tgt, spec, ctx, **_judge_inputs(tgt, spec))
    again = judge_reused(ledger, tgt, spec, ctx, **_judge_inputs(tgt, spec))
    assert again == 0
    assert len(find_events(ledger, events.REUSED_JUDGE_VERDICT)) == 2


def test_no_reuse_is_a_noop(tmp_path):
    tgt = tmp_path / "plain"
    spec, _sp, ledger = locked_experiment(tgt, judge=_FAKE_JUDGE)
    _lay_tasks(tgt)
    assert comparisons_from_reuse(ledger, tgt, spec) == []
    assert judge_reused(ledger, tgt, spec, ctx_for(tgt), **_judge_inputs(tgt, spec)) == 0
