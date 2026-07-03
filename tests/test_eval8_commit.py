"""EVAL-8 / D-6 — task-content commitment pinned at lock (PL-7, GR-5)."""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

import pytest

from harness.cli import app
from harness.corpus.commit import (
    TaskCommitmentError,
    assert_task_commitment,
    compute_commitment,
    load_task_dicts,
)
from harness.ledger import events
from harness.ledger.query import find_events
from tests.fixtures.builders import write_experiment_yaml

runner = CliRunner()

TASKS = [{"id": "t1", "prompt": "solve it"}, {"id": "t2", "prompt": "and this"}]


def _write_tasks(path, tasks):
    path.write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")


# --- unit ------------------------------------------------------------------
def test_compute_commitment_deterministic_and_order_independent():
    a = compute_commitment(TASKS, corpus_id="c", semver="1.0.0")
    b = compute_commitment(list(reversed(TASKS)), corpus_id="c", semver="1.0.0")
    assert a == b
    assert len(a["task_shas_sha256"]) == 64
    # any content change moves the hash
    changed = [{"id": "t1", "prompt": "SWAPPED"}, TASKS[1]]
    assert compute_commitment(changed, corpus_id="c", semver="1.0.0") != a


def test_assert_task_commitment_refuses_swap_and_missing():
    committed = compute_commitment(TASKS, corpus_id="c", semver="1.0.0")
    lock = {"task_commitment": committed}
    assert_task_commitment(lock, TASKS, corpus_id="c", semver="1.0.0")  # ok

    swapped = [{"id": "t1", "prompt": "HACKED"}, TASKS[1]]
    with pytest.raises(TaskCommitmentError):
        assert_task_commitment(lock, swapped, corpus_id="c", semver="1.0.0")

    # a lock with no commitment is itself a refusal
    with pytest.raises(TaskCommitmentError):
        assert_task_commitment({}, TASKS, corpus_id="c", semver="1.0.0")


def test_load_task_dicts_rejects_bad_ids(tmp_path):
    """D3: duplicate or missing task ids are refused loudly, not silently
    collapsed (which would drop a task from the commitment) or crashed with an
    opaque KeyError."""
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1"}, {"id": "t1"}]}), encoding="utf-8"
    )
    with pytest.raises(TaskCommitmentError):
        load_task_dicts(tmp_path)

    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"prompt": "no id here"}]}), encoding="utf-8"
    )
    with pytest.raises(TaskCommitmentError):
        load_task_dicts(tmp_path)


def test_self_attested_task_sha_ignored():
    # a forged task_sha field cannot fix the commitment: the sha is recomputed
    honest = compute_commitment([{"id": "t1", "prompt": "p"}], corpus_id="c", semver="1.0.0")
    forged = compute_commitment(
        [{"id": "t1", "prompt": "p", "task_sha": "deadbeef"}], corpus_id="c", semver="1.0.0"
    )
    assert honest != forged


# --- CLI integration -------------------------------------------------------
def test_plan_pins_task_commitment(tmp_path):
    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml")
    _write_tasks(expdir / "tasks.yaml", TASKS)
    ledger = expdir / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)])
    assert r.exit_code == 0, r.output
    tc = find_events(ledger, events.EXPERIMENT_LOCKED)[0]["task_commitment"]
    assert tc["corpus_id"] == "public-mini" and tc["semver"] == "1.0.0"
    assert tc == compute_commitment(TASKS, corpus_id="public-mini", semver="1.0.0")


def test_plan_without_tasks_pins_no_commitment(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")  # no tasks.yaml
    ledger = tmp_path / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    assert r.exit_code == 0, r.output
    assert "task_commitment" not in find_events(ledger, events.EXPERIMENT_LOCKED)[0]


def test_run_cli_refuses_swapped_tasks(tmp_path):
    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml")
    _write_tasks(expdir / "tasks.yaml", [{"id": "t1", "prompt": "orig", "fake_behavior": {}}])
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    # swap the task prompt after the lock
    _write_tasks(expdir / "tasks.yaml", [{"id": "t1", "prompt": "SWAPPED", "fake_behavior": {}}])
    r = runner.invoke(app, ["run", str(expdir)])
    assert r.exit_code != 0
    assert "commitment" in r.output.lower()
    # no trial was executed
    assert find_events(ledger, "trial") == []


def _plan_with_tasks(expdir, tasks):
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml")
    _write_tasks(expdir / "tasks.yaml", tasks)
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    return ledger


def test_grade_cli_refuses_swapped_tasks(tmp_path):
    expdir = tmp_path / "exp"
    ledger = _plan_with_tasks(expdir, [{"id": "t1", "prompt": "orig"}])
    _write_tasks(expdir / "tasks.yaml", [{"id": "t1", "prompt": "SWAPPED"}])
    r = runner.invoke(app, ["grade", str(expdir), "--runner", "local"])
    assert r.exit_code != 0
    assert "commitment" in r.output.lower()


def test_grade_cli_fail_closed_unknown_task_and_missing_artifacts(tmp_path):
    """GR-7: an unknown task_id or a record with no artifacts_path ledgers a
    cant_grade rather than silently skipping the trial forever."""
    from harness.ledger.events import EventContext, record_trial

    expdir = tmp_path / "exp"
    ledger = _plan_with_tasks(expdir, [{"id": "t1", "prompt": "p"}])
    ctx = EventContext(experiment_id="exp", clock=lambda: "t")
    record_trial(ledger, ctx, trial_record={
        "trial_id": "u1", "task_id": "not-a-task", "artifacts_path": "/tmp/u1/artifacts"})
    record_trial(ledger, ctx, trial_record={
        "trial_id": "m1", "task_id": "t1", "artifacts_path": ""})
    r = runner.invoke(app, ["grade", str(expdir), "--runner", "local"])
    assert r.exit_code == 0, r.output
    reasons = {e["trial_id"]: e["reason"] for e in find_events(ledger, "cant_grade")}
    assert reasons["u1"] == "unknown_task"
    assert reasons["m1"] == "artifacts_missing"
