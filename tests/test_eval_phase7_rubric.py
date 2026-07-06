"""7D-4 / D-P7-6 — the judging rubric content is committed into the lock.

Mirrors the task-commitment swap-refusal shape (test_eval8_commit): lock pins
the rubric's normalized-text hash; a post-lock swap is refused by bench judge;
plan refuses when the rubric file is absent; a legacy lock (no committed hash)
warns rather than refusing.
"""

from __future__ import annotations

import hashlib

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger import events
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, seed_trial_and_grade, write_experiment_yaml

runner = CliRunner()

_FAKE_JUDGE = {
    "model": "fake/deterministic-2026-01-01",
    "rubric": "rubric.md",
    "orders": "both",
    "temperature": 0,
}


def _plan(expdir, *, tasks=None):
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE))
    tasks = tasks or [{"id": "t1", "prompt": "solve it", "task_class": "refactor"}]
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)])
    return ledger, r


def _seed_two(ledger):
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="b", task_id="t1", arm="treatment", passed=False)


def test_lock_commits_rubric_sha256(tmp_path):
    expdir = tmp_path / "exp"
    ledger, r = _plan(expdir)
    assert r.exit_code == 0, r.output
    lock = find_events(ledger, "experiment_locked")[0]
    expected = hashlib.sha256(
        (expdir / "rubric.md").read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    assert lock["rubric_sha256"] == expected


def test_plan_refuses_missing_rubric(tmp_path):
    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE))
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8"
    )
    (expdir / "rubric.md").unlink()  # remove the rubric the fixture materialized
    ledger = expdir / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)])
    assert r.exit_code == 2
    assert "rubric" in (r.output + (r.stderr or "")).lower()
    assert find_events(ledger, "experiment_locked") == []


def test_judge_refuses_swapped_rubric(tmp_path):
    expdir = tmp_path / "exp"
    ledger, r = _plan(expdir)
    assert r.exit_code == 0
    _seed_two(ledger)
    # swap the rubric content after the lock
    (expdir / "rubric.md").write_text("A DIFFERENT rubric.", encoding="utf-8")
    rj = runner.invoke(app, ["judge", str(expdir)])
    assert rj.exit_code == 2
    assert "swapped after the lock" in (rj.output + (rj.stderr or ""))
    assert find_events(ledger, "judge_verdict") == []


def test_legacy_lock_warns_and_still_judges(tmp_path):
    """A pre-Phase-7 lock (no committed rubric hash) is never invalidated: judge
    warns instead of refusing, and still produces verdicts [D-P7-6 legacy]."""
    from harness.corpus.commit import compute_commitment, load_task_dicts
    from harness.plan.lock import spec_sha256
    from harness.plan.power import AssumedVariance, mde_check
    from harness.schema.experiment import ExperimentSpec

    expdir = tmp_path / "exp"
    expdir.mkdir()
    spec_path = write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE))
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "solve it", "task_class": "refactor"}]}),
        encoding="utf-8",
    )
    ledger = expdir / "ledger.ndjson"
    ctx = fixed_ctx(experiment_id="exp")
    spec = ExperimentSpec.from_yaml(spec_path)
    mde = mde_check(spec, AssumedVariance(), n_sim=8, n_boot=40, deltas=[0.2, 0.4])
    tc = compute_commitment(
        load_task_dicts(expdir), corpus_id=spec.corpus.id, semver=spec.corpus.version
    )
    # a LEGACY lock event — no rubric_sha256 field
    events.record_experiment_locked(
        ledger, ctx, spec_sha256=spec_sha256(spec_path), spec_path=str(spec_path),
        seed=spec.seed, mde=mde.to_event_payload(), attested_by="t", method="m", task_commitment=tc,
    )
    _seed_two(ledger)

    rj = runner.invoke(app, ["judge", str(expdir)])
    assert rj.exit_code == 0, rj.output
    assert "predates rubric commitment" in (rj.output + (rj.stderr or ""))
    assert len(find_events(ledger, "judge_verdict")) == 1
