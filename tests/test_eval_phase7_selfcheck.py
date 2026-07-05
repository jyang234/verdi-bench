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
    method whose empirical coverage falls within the Wilson band around 0.95 —
    on the INDEPENDENT validation stream [F-M-S1], so the fixture must be
    genuinely calibrated, not winner's-curse-flattered (16 clusters; the old
    10-cluster fixture only passed on the shared draws, see the F-M-S1 test)."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    for i in range(16):
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


def _seed_matching_selfcheck(ledger, ctx, spec, *, n_sim=20, n_boot=200):
    """Seed a passing, current selfcheck whose validated method matches the
    deployed selection — via the real run_selfcheck (same seed + params), forced
    to pass. Seed BEFORE compute_findings (findings are head-bound)."""
    res = run_selfcheck(ledger, spec, n_sim=n_sim, n_boot=n_boot)
    res["passed"] = True
    record_selfcheck(ledger, ctx, **res)


def test_official_fence_passes_with_passed_selfcheck(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    _seed_full_calibration(ledger, ctx)
    _seed_matching_selfcheck(ledger, ctx, spec)  # current & matching, before compute
    assert selfcheck_passed(ledger) is True
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(),
                         coverage_n_sim=20, n_boot=200)
    md = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "Official findings" in md


def test_official_fence_refuses_stale_selfcheck(tmp_path):
    """review #1: a passing selfcheck that predates later trials/grades is stale —
    the official render is refused until selfcheck is re-run on the current data.
    Scenario: selfcheck(pass) → more grades → analyze (findings recomputed on the
    new data, so the head-hash check passes, but the selfcheck is now stale)."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    _seed_full_calibration(ledger, ctx)
    _seed_matching_selfcheck(ledger, ctx, spec)  # passes on the data so far
    # append data AFTER the selfcheck, on an already-admitted task so checks 1-3
    # still pass; then compute findings on the NEW head so the head-hash check is
    # satisfied and the fence reaches the staleness check (5).
    seed_trial_and_grade(ledger, ctx, trial_id="late-c", task_id="task0", arm="control", passed=True)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(),
                         coverage_n_sim=20, n_boot=200)
    with pytest.raises(SelfcheckRequiredError) as exc:
        render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "predates" in str(exc.value)


def test_official_fence_refuses_method_mismatch(tmp_path):
    """review #2: a passing, current selfcheck that validated a DIFFERENT CI
    method than the render deploys is refused — the certified coverage is not the
    coverage of the interval shown."""
    from harness.ledger.query import ledger_head_hash  # noqa: F401 (clarity)

    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    _seed_full_calibration(ledger, ctx)
    # peek at the method the render will deploy, then seed a current selfcheck
    # claiming a DIFFERENT method (still before the real compute, so head-bound).
    peek = compute_findings(ledger, spec, spec.seed, coverage_n_sim=20, n_boot=200)
    deployed = peek.ci_selection["selected_method"]
    wrong = "bca" if deployed != "bca" else "percentile"
    record_selfcheck(ledger, ctx, selected_method=wrong, nominal=0.95, coverage=0.95,
                     mc_interval=[0.9, 1.0], n_sim=20, n_boot=200, n_tasks=5,
                     null_model="paired_binary", passed=True)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(),
                         coverage_n_sim=20, n_boot=200)
    with pytest.raises(SelfcheckRequiredError) as exc:
        render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "deploys" in str(exc.value)


def test_selfcheck_validates_the_deployed_method(tmp_path):
    """review #2 root: run_selfcheck seeds with spec.seed (not a sub-seed), so the
    method it validates is exactly the method compute_findings deploys."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, treatment_pass=lambda i: i % 2 == 0)
    res = run_selfcheck(ledger, spec, n_sim=40, n_boot=500)
    f = compute_findings(ledger, spec, spec.seed, coverage_n_sim=40, n_boot=500)
    assert res["selected_method"] == f.ci_selection["selected_method"]


def test_m_s1_selfcheck_validates_on_a_fresh_stream(tmp_path):
    """F-M-S1: selection and validation previously shared the same 200 draws,
    so the coverage-closest-to-nominal winner was scored on the draws that
    crowned it — a winner's-curse bias toward passing. The 10-cluster fixture
    demonstrates it concretely: the selection-stream estimate sits inside the
    Wilson band (the OLD gate passed), while the independent validation
    estimate does not (the honest gate refuses)."""
    from harness.analyze.selfcheck import run_selfcheck, wilson_interval

    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    for i in range(10):
        for r in range(2):
            seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}-{r}", task_id=f"t{i}",
                                 arm="control", repetition=r, passed=True)
            seed_trial_and_grade(ledger, ctx, trial_id=f"x-{i}-{r}", task_id=f"t{i}",
                                 arm="treatment", repetition=r, passed=(i % 2 == 0))
    res = run_selfcheck(ledger, spec, n_sim=200, n_boot=800)
    # the old shared-draw gate would have passed this fixture...
    sel_lo, sel_hi = wilson_interval(res["coverage"], res["n_sim"])
    assert sel_lo <= res["nominal"] <= sel_hi
    # ...the independent validation stream refuses it, and the event carries
    # both figures so the divergence is auditable.
    assert res["passed"] is False
    assert res["validation_coverage"] is not None
    assert res["validation_n_sim"] == 400
    assert res["validation_coverage"] != res["coverage"]
    # deterministic: same ledger => byte-identical payload, fresh stream or not
    assert run_selfcheck(ledger, spec, n_sim=200, n_boot=800) == res
