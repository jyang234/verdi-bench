"""7I / EVAL-1-D008 — the coverage selfcheck + official-render gate.

The selfcheck is deterministic in the locked seed, ledgers exactly one additive
`selfcheck` event, and the official fence refuses until a passed selfcheck
exists.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from harness.analyze.report import (
    SelfcheckRequiredError,
    compute_findings,
    render_markdown,
)
from harness.analyze.selfcheck import run_selfcheck, selfcheck_passed, wilson_interval
from harness.cli import app
from harness.ledger.events import record_selfcheck
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

runner = CliRunner()


def _full_corpus():
    from harness.corpus.registry import CorpusManifest, TaskEntry

    m = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha="a" * 64, status="admitted") for i in range(5)],
    )
    m.calibration.status = "full-run-validated"
    return m


def _populate(ledger, ctx, *, treatment_pass):
    for i in range(5):
        for r in range(2):
            seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}-{r}", task_id=f"task{i}",
                                 arm="control", repetition=r, passed=True,
                                 provenance={"image_digest": "d"})
            seed_trial_and_grade(ledger, ctx, trial_id=f"x-{i}-{r}", task_id=f"task{i}",
                                 arm="treatment", repetition=r, passed=treatment_pass(i),
                                 provenance={"image_digest": "d"})


def _seed_full_calibration(ledger, ctx):
    from harness.ledger.events import record_calibration_run

    record_calibration_run(ledger, ctx, corpus_id="public-mini", semver="1.0.0",
                           kind="full", run={"p": 0.5, "rho": 0.3, "n_tasks": 5},
                           status="full-run-validated")


def test_wilson_interval_self_scales_with_n():
    """Sanity: the Wilson band tightens as n grows (self-scaling, no magic tol)."""
    lo1, hi1 = wilson_interval(0.95, 50)
    lo2, hi2 = wilson_interval(0.95, 5000)
    assert (hi1 - lo1) > (hi2 - lo2)


def test_selfcheck_is_deterministic(tmp_path):
    """Same ledger ⇒ byte-identical selfcheck payload (seed from the locked seed)."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    a = run_selfcheck(ledger, spec, n_sim=100, n_boot=200)
    b = run_selfcheck(ledger, spec, n_sim=100, n_boot=200)
    assert a == b


def test_well_powered_fixture_passes(tmp_path):
    """A well-powered fixture with genuine per-task variation lands a selected
    method whose empirical coverage falls within the Wilson band around 0.95."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    # 10 task clusters, control always passes, treatment alternates — enough
    # non-degenerate deltas for a well-calibrated interval under the null.
    for i in range(10):
        for r in range(2):
            seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}-{r}", task_id=f"t{i}",
                                 arm="control", repetition=r, passed=True)
            seed_trial_and_grade(ledger, ctx, trial_id=f"x-{i}-{r}", task_id=f"t{i}",
                                 arm="treatment", repetition=r, passed=(i % 2 == 0))
    res = run_selfcheck(ledger, spec, n_sim=200, n_boot=800)
    assert res["null_model"] == "paired_binary"
    assert res["passed"] is True


def test_starved_fixture_fails_insufficient(tmp_path):
    """< 2 realized clusters ⇒ insufficient_data, passed=false — an experiment
    too small to selfcheck cannot render official [D008 (b)]."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    seed_trial_and_grade(ledger, ctx, trial_id="c0", task_id="t0", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="x0", task_id="t0", arm="treatment", passed=False)
    res = run_selfcheck(ledger, spec, n_sim=50, n_boot=100)
    assert res["null_model"] == "insufficient_data"
    assert res["passed"] is False


def test_bench_selfcheck_verb_ledgers_one_event(tmp_path):
    import yaml

    expdir = tmp_path / "e"
    spec, _, ledger = locked_experiment(expdir, ctx=fixed_ctx())
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")
    _populate(ledger, fixed_ctx(), treatment_pass=lambda i: i % 2 == 0)
    r = runner.invoke(app, ["selfcheck", str(expdir)])
    assert r.exit_code == 0, r.output
    assert len(find_events(ledger, "selfcheck")) == 1


def test_official_fence_refused_without_selfcheck(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    _seed_full_calibration(ledger, ctx)  # calibration ok, but no selfcheck yet
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(),
                         coverage_n_sim=20, n_boot=200)
    with pytest.raises(SelfcheckRequiredError) as exc:
        render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "bench selfcheck" in str(exc.value)


def test_official_fence_refused_with_failed_selfcheck(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    _seed_full_calibration(ledger, ctx)
    record_selfcheck(ledger, ctx, selected_method="percentile", nominal=0.95,
                     coverage=None, mc_interval=None, n_sim=0, n_boot=0, n_tasks=1,
                     null_model="insufficient_data", passed=False)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(),
                         coverage_n_sim=20, n_boot=200)
    with pytest.raises(SelfcheckRequiredError):
        render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())


def test_official_fence_passes_with_passed_selfcheck(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    _seed_full_calibration(ledger, ctx)
    record_selfcheck(ledger, ctx, selected_method="bca", nominal=0.95, coverage=0.94,
                     mc_interval=[0.9, 0.97], n_sim=200, n_boot=1000, n_tasks=5,
                     null_model="paired_binary", passed=True)
    assert selfcheck_passed(ledger) is True
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(),
                         coverage_n_sim=20, n_boot=200)
    md = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "Official findings" in md
