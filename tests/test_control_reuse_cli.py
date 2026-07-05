"""End-to-end control reuse through the bench CLI [control-reuse plan, slice 7].

plan + run a source, export its control arm, then `bench run --reuse-control`
on an identical target: the contender runs fresh, the control is imported (not
re-run), and preflight refuses a drifted target.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger.query import find_events
from tests.fixtures.builders import write_experiment_yaml

runner = CliRunner()

_TASKS = [{"id": "t1", "prompt": "solve", "fake_behavior": {"native_log": {"total_cost_usd": 0.01}}}]


def _plan_and_run(expdir):
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml")
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": _TASKS}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    return ledger


def test_export_then_reuse_through_cli(tmp_path):
    # 1. source: plan + run both arms
    src = tmp_path / "src"
    src_ledger = _plan_and_run(src)
    assert runner.invoke(app, ["run", str(src)]).exit_code == 0
    assert len(find_events(src_ledger, "trial")) == 6  # 2 arms x 3 reps x 1 task

    # 2. export the control arm
    bundle = tmp_path / "control.bundle.json"
    r = runner.invoke(app, ["control-cache", "export", str(src), "--arm", "control", "--out", str(bundle)])
    assert r.exit_code == 0, r.output
    assert bundle.exists()

    # 3. target: plan + run with reuse — contender runs fresh, control imported
    tgt = tmp_path / "tgt"
    tgt_ledger = _plan_and_run(tgt)
    r = runner.invoke(app, ["run", str(tgt), "--reuse-control", str(bundle)])
    assert r.exit_code == 0, r.output
    assert "reusing control arm 'control'" in r.output

    assert len(find_events(tgt_ledger, "control_reused")) == 1
    assert len(find_events(tgt_ledger, "reused_trial")) == 3  # control, from bundle
    native = find_events(tgt_ledger, "trial")
    assert len(native) == 3  # only the contender ran fresh
    assert {ev["trial_record"]["arm"] for ev in native} == {"treatment"}
    assert runner.invoke(app, ["verify-chain", str(tgt_ledger)]).exit_code == 0


def test_reuse_refuses_drifted_target(tmp_path):
    src = tmp_path / "src"
    _plan_and_run(src)
    assert runner.invoke(app, ["run", str(src)]).exit_code == 0
    bundle = tmp_path / "control.bundle.json"
    assert runner.invoke(
        app, ["control-cache", "export", str(src), "--arm", "control", "--out", str(bundle)]
    ).exit_code == 0

    # target with a DIFFERENT task prompt — fingerprint must not match
    tgt = tmp_path / "tgt"
    tgt.mkdir()
    write_experiment_yaml(tgt / "experiment.yaml")
    drifted = [{"id": "t1", "prompt": "SOMETHING ELSE", "fake_behavior": {"native_log": {}}}]
    (tgt / "tasks.yaml").write_text(yaml.safe_dump({"tasks": drifted}), encoding="utf-8")
    ledger = tgt / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(tgt / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    r = runner.invoke(app, ["run", str(tgt), "--reuse-control", str(bundle)])
    assert r.exit_code == 2
    assert "provably unchanged" in r.output and "t1" in r.output
    assert find_events(ledger, "control_reused") == []


def test_export_unknown_arm_errors(tmp_path):
    src = tmp_path / "src"
    _plan_and_run(src)
    assert runner.invoke(app, ["run", str(src)]).exit_code == 0
    r = runner.invoke(
        app, ["control-cache", "export", str(src), "--arm", "ghost", "--out", str(tmp_path / "b.json")]
    )
    assert r.exit_code == 2
    assert "not declared" in r.output
