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


def test_pr5_analyze_render_surfaces_kappa_and_correlations(tmp_path):
    """The analyze process section carries per-dimension judge↔human kappa [AC-5],
    score-vs-telemetry correlations and style_only flags [AC-7], and the render
    shows them — none of which existed before Phase 4 (PR-5)."""
    import json as _json

    from harness.analyze.report import compute_findings, render_markdown
    from harness.ledger.events import record_grade
    from harness.process.rubric import default_rubric
    from harness.schema.experiment import ExperimentSpec

    expdir = tmp_path / "exp"
    ledger = _plan(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    # two arms of one comparison, each with a transcript + telemetry + grade
    for trial_id, arm, passed, calls in [("tr-a", "control", True, 2), ("tr-b", "treatment", False, 9)]:
        _seed_trial_with_transcript(ledger, ctx, trial_id=trial_id, task_id="t1", arm=arm,
                                    workspace=tmp_path / f"ws-{trial_id}",
                                    transcript=f"work by {arm[:0]} run {calls}")
        record_grade(ledger, ctx, trial_id=trial_id, task_sha="sha-t1",
                     assertions=[{"id": "h1", "source": "holdout_test",
                                  "result": "pass" if passed else "fail"}],
                     binary_score=passed)

    # full flow through bench verbs: judge -> review build -> record -> reveal
    assert runner.invoke(app, ["judge", str(expdir)]).exit_code == 0
    assert runner.invoke(app, ["review", "build", str(expdir)]).exit_code == 0
    assert runner.invoke(app, [
        "review", "record", str(expdir), "--comparison-id", "cmp-t1-r0", "--winner", "1",
    ]).exit_code == 0
    assert runner.invoke(app, [
        "review", "reveal", str(expdir), "--comparison-id", "cmp-t1-r0",
    ]).exit_code == 0
    # judge process scores, then a human process score for tr-a (post-reveal)
    assert runner.invoke(app, ["process", "score", str(expdir)]).exit_code == 0
    scores_file = expdir / "human_scores.json"
    scores_file.write_text(
        _json.dumps({d: 4 for d in default_rubric().dimension_ids}), encoding="utf-8"
    )
    assert runner.invoke(app, [
        "process", "record", str(expdir), "--trial-id", "tr-a",
        "--comparison-id", "cmp-t1-r0", "--scores", str(scores_file),
    ]).exit_code == 0

    spec = ExperimentSpec.from_yaml(expdir / "experiment.yaml")
    findings = compute_findings(ledger, spec, spec.seed, coverage_n_sim=20, coverage_n_boot=40, n_boot=100)
    proc = findings.process
    assert proc is not None
    # AC-5: per-dimension judge<->human kappa is present and computed over >=1 pair
    kappa = proc["kappa_by_dimension"]
    assert kappa and any(k["sufficient"] for k in kappa.values())
    # AC-7: score-vs-telemetry correlations + style_only key present
    assert "correlations" in proc and "style_only" in proc
    md = render_markdown(findings, ledger, "exploratory")
    assert "judge↔human agreement" in md
    assert "score-vs-telemetry correlation" in md
