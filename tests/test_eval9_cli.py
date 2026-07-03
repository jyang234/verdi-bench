"""EVAL-9 PR-5 — the ``bench process score`` verb.

Before Phase 4 ``bench process`` registered only ``record``; ``score_trial_process``
had no CLI. This drives ``bench process score`` over graded trials and asserts it
appends judge process_score events with numeric per-dimension scores.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, write_experiment_yaml

runner = CliRunner()

_FAKE_JUDGE = {
    "model": "fake/deterministic-2026-01-01",
    "rubric": "rubric.md",
    "orders": "both",
    "temperature": 0,
}


def _plan(expdir):
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE))
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "solve it", "task_class": "refactor"}]}),
        encoding="utf-8",
    )
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    return ledger


def _seed_trial_with_transcript(ledger, ctx, *, trial_id, task_id, arm, workspace, transcript):
    from harness.adapters.base import Outcome, Provenance, Telemetry, TrialRecord
    from harness.ledger.events import record_trial

    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "transcript.txt").write_text(transcript, encoding="utf-8")
    rec = TrialRecord.assemble(
        trial_id=trial_id, task_id=task_id, arm=arm, repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(tool_calls=3, wall_time_s=12.0),
        provenance=Provenance(), artifacts_path=str(artifacts),
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))


def test_pr5_bench_process_score_verb(tmp_path):
    expdir = tmp_path / "exp"
    ledger = _plan(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    _seed_trial_with_transcript(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control",
                                workspace=tmp_path / "wsa", transcript="ran tests, all pass")
    _seed_trial_with_transcript(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment",
                                workspace=tmp_path / "wsb", transcript="edited files, skipped tests")

    r = runner.invoke(app, ["process", "score", str(expdir)])
    assert r.exit_code == 0, r.output
    scores = find_events(ledger, "process_score")
    assert len(scores) == 2
    ps = scores[0]["process_score"]
    assert ps["comparison_id"] == "cmp-t1-r0"
    assert ps["provenance"]["unblinded"] is True
    # every dimension got a numeric 1..5 score (no fabricated network call)
    for ds in ps["scores"]:
        assert ds["score"] is None or 1 <= ds["score"] <= 5
    assert any(ds["score"] is not None for ds in ps["scores"])
    assert runner.invoke(app, ["verify-chain", str(ledger)]).exit_code == 0


def test_pr5_process_score_idempotent(tmp_path):
    """A second run does not re-score an already-scored trial."""
    expdir = tmp_path / "exp"
    ledger = _plan(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    _seed_trial_with_transcript(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control",
                                workspace=tmp_path / "wsa", transcript="x")
    assert runner.invoke(app, ["process", "score", str(expdir)]).exit_code == 0
    assert runner.invoke(app, ["process", "score", str(expdir)]).exit_code == 0
    assert len(find_events(ledger, "process_score")) == 1  # not re-scored
