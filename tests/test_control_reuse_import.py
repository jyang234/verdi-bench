"""Control-reuse preflight import + schedule filter [control-reuse plan, slice 4].

A matching bundle imports into the target ledger under the reused_* kinds and
stashes diffs; a drifted target refuses loudly; re-import is idempotent; the
scheduler drops the reused arm's cells.
"""

from __future__ import annotations

import pytest
import yaml

from harness.ledger import events
from harness.ledger.query import find_events
from harness.plan.interleave import Trial
from harness.run.control_reuse import ControlReuseFingerprintError
from harness.run.reuse import (
    build_bundle,
    filter_reused_cells,
    import_bundle,
    reused_diff_path,
)
from harness.run.settings import load_run_settings
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

_TASKS = {"tasks": [
    {"id": "t1", "prompt": "p1", "holdouts_dir": "holdouts/t1", "plugins": ["groundwork"]},
    {"id": "t2", "prompt": "p2", "holdouts_dir": "holdouts/t2"},
]}


def _lay_tasks(exp_dir):
    (exp_dir / "tasks.yaml").write_text(yaml.safe_dump(_TASKS), encoding="utf-8")
    for tid, body in (("t1", "assert a"), ("t2", "assert b")):
        d = exp_dir / "holdouts" / tid
        d.mkdir(parents=True)
        (d / "holdout.json").write_text(body, encoding="utf-8")


def _source(tmp_path):
    src = tmp_path / "src-exp"
    _spec, _sp, ledger = locked_experiment(src)
    _lay_tasks(src)
    ctx = fixed_ctx(experiment_id="src-exp")
    for tid in ("t1", "t2"):
        seed_trial_and_grade(ledger, ctx, trial_id=f"tr-{tid}", task_id=tid, arm="control")
    return src


def _target(tmp_path):
    """A byte-identical experiment that has NOT run the control arm."""
    tgt = tmp_path / "tgt-exp"
    spec, _sp, ledger = locked_experiment(tgt)
    _lay_tasks(tgt)
    return tgt, spec, ledger


def test_matching_bundle_imports(tmp_path):
    bundle = build_bundle(_source(tmp_path), "control")
    tgt, spec, ledger = _target(tmp_path)
    settings = load_run_settings(tgt, spec=spec)
    arm = import_bundle(tgt, bundle, fixed_ctx(experiment_id="tgt-exp"),
                        engine="fake", spec=spec, settings=settings)
    assert arm == "control"
    assert len(find_events(ledger, events.CONTROL_REUSED)) == 1
    assert len(find_events(ledger, events.REUSED_TRIAL)) == 2
    assert len(find_events(ledger, events.REUSED_GRADE)) == 2
    # native queries stay empty — official path can't see reused data
    assert find_events(ledger, events.TRIAL) == []
    # diff snapshots stashed on disk
    assert reused_diff_path(tgt, "tr-t1").exists()


def test_reimport_is_idempotent(tmp_path):
    bundle = build_bundle(_source(tmp_path), "control")
    tgt, spec, ledger = _target(tmp_path)
    settings = load_run_settings(tgt, spec=spec)
    ctx = fixed_ctx(experiment_id="tgt-exp")
    import_bundle(tgt, bundle, ctx, engine="fake", spec=spec, settings=settings)
    import_bundle(tgt, bundle, ctx, engine="fake", spec=spec, settings=settings)  # resume
    assert len(find_events(ledger, events.CONTROL_REUSED)) == 1
    assert len(find_events(ledger, events.REUSED_TRIAL)) == 2


def test_holdout_drift_refuses_import(tmp_path):
    bundle = build_bundle(_source(tmp_path), "control")
    tgt, spec, ledger = _target(tmp_path)
    # the target's holdout bytes drifted from the source — reuse is invalid
    (tgt / "holdouts" / "t1" / "holdout.json").write_text("assert TAMPERED", encoding="utf-8")
    settings = load_run_settings(tgt, spec=spec)
    with pytest.raises(ControlReuseFingerprintError, match=r"holdout script bytes"):
        import_bundle(tgt, bundle, fixed_ctx(experiment_id="tgt-exp"),
                      engine="fake", spec=spec, settings=settings)
    assert find_events(ledger, events.CONTROL_REUSED) == []  # nothing imported


def test_engine_drift_refuses_import(tmp_path):
    bundle = build_bundle(_source(tmp_path), "control")  # source engine = fake
    tgt, spec, ledger = _target(tmp_path)
    settings = load_run_settings(tgt, spec=spec)
    with pytest.raises(ControlReuseFingerprintError, match=r"operational environment"):
        import_bundle(tgt, bundle, fixed_ctx(experiment_id="tgt-exp"),
                      engine="harbor", spec=spec, settings=settings)


def test_filter_drops_reused_arm_cells():
    order = [
        Trial(task_id="t1", arm="control", repetition=0),
        Trial(task_id="t1", arm="treatment", repetition=0),
        Trial(task_id="t2", arm="control", repetition=0),
        Trial(task_id="t2", arm="treatment", repetition=0),
    ]
    filtered = filter_reused_cells(order, "control")
    assert [t.arm for t in filtered] == ["treatment", "treatment"]
