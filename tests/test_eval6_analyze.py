"""EVAL-6 — analyze: paired stats, effect sizes, confounds, the fence, provenance."""

from __future__ import annotations

import pytest

from harness.analyze.confounds import flag_confounds
from harness.analyze.effect import cliffs_delta, effect_sizes
from harness.analyze.report import (
    CalibrationIncompleteError,
    ProvenanceError,
    UnregisteredOfficialError,
    compute_findings,
    render_html,
    render_markdown,
)
from harness.analyze.stats import paired_bootstrap
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.ledger.events import (
    record_executed_order,
    record_trial_infra_failed,
)
from harness.ledger.query import ledger_head_hash, verify
from tests.fixtures.builders import (
    fixed_ctx,
    locked_experiment,
    seed_trial_and_grade,
)

_FAST = dict(coverage_n_sim=40, coverage_n_boot=100, n_boot=500)


def _full_corpus():
    m = CorpusManifest(
        corpus_id="terminal-bench", semver="2.0.0", kind="public",
        tasks=[TaskEntry(task_id="task0", sha="a" * 64, status="admitted")],
    )
    m.calibration.status = "full-run-validated"
    return m


def _populate(ledger, ctx, *, control_pass, treatment_pass, tasks=5, reps=2,
              control_tel=None, treatment_tel=None, control_prov=None, treatment_prov=None):
    control_tel = control_tel if control_tel is not None else {"cost": 1.0, "wall_time_s": 10.0}
    treatment_tel = treatment_tel if treatment_tel is not None else {"cost": 1.1, "wall_time_s": 9.0}
    for i in range(tasks):
        for rep in range(reps):
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"c-{i}-{rep}", task_id=f"task{i}", arm="control",
                repetition=rep, passed=control_pass(i), telemetry=control_tel,
                provenance=control_prov or {"image_digest": "digestC"},
            )
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"t-{i}-{rep}", task_id=f"task{i}", arm="treatment",
                repetition=rep, passed=treatment_pass(i), telemetry=treatment_tel,
                provenance=treatment_prov or {"image_digest": "digestT"},
            )


# --- AC-1: paired bootstrap + reproducibility --------------------------------
def test_ac1_paired_bootstrap():
    r = paired_bootstrap([0.1, 0.2, 0.15, 0.05, 0.3], seed=1234)
    assert r.n_tasks == 5 and r.n_boot == 10_000
    assert r.ci_low <= r.mean_delta <= r.ci_high
    assert abs(r.mean_delta - 0.16) < 1e-9


def test_ac1_reproducible_seeded(tmp_path):
    # bootstrap is byte-identical for a fixed (deltas, seed)
    a = paired_bootstrap([0.1, -0.2, 0.3], seed=42, ci_method="bca")
    b = paired_bootstrap([0.1, -0.2, 0.3], seed=42, ci_method="bca")
    assert a.as_dict() == b.as_dict()

    # whole findings document is byte-identical for a fixed (ledger, seed)
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: i % 2 == 0, treatment_pass=lambda i: True)
    f1 = compute_findings(ledger, spec, spec.seed, **_FAST)
    f2 = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f1.model_dump_json() == f2.model_dump_json()


# --- AC-2: effect sizes (hand-checked) --------------------------------------
def test_ac2_effect_sizes():
    # Cliff's delta hand-check: A=[3,4,5] fully dominates B=[1,2,3] except one tie
    assert abs(cliffs_delta([3, 4, 5], [1, 2, 3]) - 8 / 9) < 1e-12
    assert cliffs_delta([1, 2], [3, 4]) == -1.0  # A always below B
    assert cliffs_delta([5, 6], [5, 6]) == 0.0   # symmetric

    es = effect_sizes([1.0, 1.0, 1.0], [0.0, 1.0, 0.0])
    assert abs(es.mean_paired_delta - (2 / 3)) < 1e-12
    # both effect sizes are mandatory and present
    assert set(es.as_dict()) == {"mean_paired_delta", "cliffs_delta"}


# --- AC-3: MDE in report + null phrasing ------------------------------------
def test_ac3_mde_in_report(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    md = render_markdown(f, ledger, "exploratory")
    assert "MDE =" in md
    assert "Minimum detectable effect" in md


def test_ac3_null_phrasing(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    # identical arms ⇒ per-task deltas all 0 ⇒ CI contains 0 ⇒ null
    _populate(ledger, ctx, control_pass=lambda i: i % 2 == 0,
              treatment_pass=lambda i: i % 2 == 0)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    md = render_markdown(f, ledger, "exploratory")
    assert "no effect ≥ MDE detected".lower() in md.lower()


# --- AC-4: confound flags (one per constructed fixture; clean ⇒ none) --------
def _clean(tmp_path, name="clean"):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / name, ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True,
              control_tel={"cost": 1.0, "wall_time_s": 10.0, "tokens_in": 5, "tokens_out": 5,
                           "tokens_cache": 1, "tool_calls": 2},
              treatment_tel={"cost": 1.0, "wall_time_s": 10.0, "tokens_in": 5, "tokens_out": 5,
                             "tokens_cache": 1, "tool_calls": 2})
    return ctx, spec, ledger


def test_ac4_clean_fixture_no_flags(tmp_path):
    _, spec, ledger = _clean(tmp_path)
    assert flag_confounds(ledger, spec) == []


def _flags(ledger, spec):
    return {c["flag"] for c in flag_confounds(ledger, spec)}


def test_ac4_flags_emitted_interleave(tmp_path):
    ctx, spec, ledger = _clean(tmp_path, "il")
    # all control first, then all treatment ⇒ maximal position skew
    order = [{"arm": "control", "outcome": "completed"} for _ in range(5)]
    order += [{"arm": "treatment", "outcome": "completed"} for _ in range(5)]
    record_executed_order(ledger, ctx, order=order)
    assert _flags(ledger, spec) == {"interleave_imbalance"}


def test_ac4_flags_emitted_provider_error(tmp_path):
    ctx, spec, ledger = _clean(tmp_path, "pe")
    for i in range(3):
        record_trial_infra_failed(
            ledger, ctx, trial_id=f"if-{i}", task_id="task0", arm="control", reason="boom"
        )
    assert _flags(ledger, spec) == {"provider_error_asymmetry"}


def test_ac4_flags_emitted_telemetry_null(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "tn", ctx=ctx)
    # control omits tokens_in; treatment has it ⇒ asymmetric null on tokens_in only
    _populate(
        ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True,
        control_tel={"cost": 1.0, "wall_time_s": 10.0, "tokens_out": 5, "tokens_cache": 1,
                     "tool_calls": 2},
        treatment_tel={"cost": 1.0, "wall_time_s": 10.0, "tokens_in": 5, "tokens_out": 5,
                       "tokens_cache": 1, "tool_calls": 2},
    )
    flags = flag_confounds(ledger, spec)
    assert _flags(ledger, spec) == {"telemetry_null_asymmetry"}
    assert flags[0]["fields"] == ["tokens_in"]


def test_ac4_flags_emitted_egress(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "eg", ctx=ctx)
    tel = {"cost": 1.0, "wall_time_s": 10.0, "tokens_in": 5, "tokens_out": 5,
           "tokens_cache": 1, "tool_calls": 2}
    seed_trial_and_grade(ledger, ctx, trial_id="c-0", task_id="task0", arm="control",
                         telemetry=tel, egress_violation=True,
                         provenance={"image_digest": "d"})
    seed_trial_and_grade(ledger, ctx, trial_id="t-0", task_id="task0", arm="treatment",
                         telemetry=tel, provenance={"image_digest": "d"})
    assert _flags(ledger, spec) == {"egress_violations"}


def test_ac4_flags_emitted_version_drift(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "vd", ctx=ctx)
    tel = {"cost": 1.0, "wall_time_s": 10.0, "tokens_in": 5, "tokens_out": 5,
           "tokens_cache": 1, "tool_calls": 2}
    # control runs two different image digests within the arm
    seed_trial_and_grade(ledger, ctx, trial_id="c-0", task_id="task0", arm="control",
                         telemetry=tel, provenance={"image_digest": "digestA"})
    seed_trial_and_grade(ledger, ctx, trial_id="c-1", task_id="task1", arm="control",
                         telemetry=tel, provenance={"image_digest": "digestB"})
    seed_trial_and_grade(ledger, ctx, trial_id="t-0", task_id="task0", arm="treatment",
                         telemetry=tel, provenance={"image_digest": "digestT"})
    assert _flags(ledger, spec) == {"version_drift"}


def test_ac4_flags_emitted_judge_vendor_overlap(tmp_path):
    ctx = fixed_ctx()
    # judge vendor (anthropic) overlaps control arm's vendor (anthropic)
    spec, _, ledger = locked_experiment(
        tmp_path / "jv", ctx=ctx,
        judge={"model": "anthropic/claude-3-5-haiku-20241022",
               "rubric": "rubrics/code-task-v1.md", "orders": "both", "temperature": 0},
    )
    assert _flags(ledger, spec) == {"judge_vendor_overlap"}


# --- AC-5: fence — unregistered refused + exploratory watermark -------------
def test_ac5_unregistered_refused(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    # official render for a non-primary metric is refused
    with pytest.raises(UnregisteredOfficialError):
        render_markdown(f, ledger, "official", metric="cost_per_task",
                        corpus_manifest=_full_corpus())
    # official render before full-run calibration is refused
    with pytest.raises(CalibrationIncompleteError):
        render_markdown(f, ledger, "official", corpus_manifest=CorpusManifest(
            corpus_id="c", semver="1.0.0", kind="public", tasks=[]))


def test_an3_refused_official_render_ledgers_cant_analyze(tmp_path):
    # AN-3: an official render before full-run calibration used to escape the CLI
    # with zero events; it must now land exactly one cant_analyze event, write no
    # findings files, and exit non-zero.
    from typer.testing import CliRunner

    from harness.cli import app
    from harness.ledger.query import find_events

    ctx = fixed_ctx()
    _, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    exp_dir = tmp_path / "e"
    before = len(find_events(ledger, "cant_analyze"))
    result = CliRunner().invoke(app, ["analyze", str(exp_dir), "--official"])  # no --corpus
    assert result.exit_code == 2
    cant = find_events(ledger, "cant_analyze")
    assert len(cant) - before == 1
    assert cant[-1]["reason"] == "calibration_incomplete"
    assert cant[-1]["mode"] == "official"
    # no findings artifacts written on the refusal path
    assert not (exp_dir / "findings.json").exists()
    assert not list(exp_dir.glob("findings.official.*"))
    # and no success event either
    assert find_events(ledger, "findings_rendered") == []


def test_an3_successful_render_event_before_files(tmp_path):
    # AN-3: the success path emits findings_rendered and writes both files.
    from typer.testing import CliRunner

    from harness.cli import app
    from harness.ledger.query import find_events

    ctx = fixed_ctx()
    _, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    exp_dir = tmp_path / "e"
    result = CliRunner().invoke(app, ["analyze", str(exp_dir), "--exploratory"])
    assert result.exit_code == 0, result.output
    assert len(find_events(ledger, "findings_rendered")) == 1
    assert (exp_dir / "findings.json").exists()
    assert (exp_dir / "findings.exploratory.md").exists()


def test_ac5_official_happy_path(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    md = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "Official findings" in md
    assert "EXPLORATORY" not in md  # official carries no watermark
    assert spec.primary_metric.value in md


def test_ac5_exploratory_watermark(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    md = render_markdown(f, ledger, "exploratory")
    # every markdown section (### header) is preceded by a watermark line
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("### "):
            assert any("EXPLORATORY" in lines[j] for j in range(max(0, i - 1), i)), line

    # HTML: the watermark banner appears before every section header
    html = render_html(f, ledger, "exploratory")
    assert html.count("watermark") >= 2
    assert "<!doctype html>" in html


# --- AC-6: finding provenance (incl head-hash == verify_chain) ---------------
def test_ac6_finding_provenance(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    p = f.provenance
    assert p.instrument_version and p.instrument_git_sha
    assert p.corpus["corpus_id"] == "terminal-bench"
    assert "task_shas" in p.corpus  # EVAL-8 semver + shas cited end-to-end [AC-6]
    # recorded head hash matches verify_chain output at compute time
    assert verify(ledger).ok
    assert p.ledger_head_hash == ledger_head_hash(ledger)


def test_ac6_stale_findings_refused(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    # mutate the ledger head after computing ⇒ render cross-check fails [AC-6]
    record_trial_infra_failed(ledger, ctx, trial_id="x", task_id="task0", arm="control",
                              reason="late")
    with pytest.raises(ProvenanceError):
        render_markdown(f, ledger, "exploratory")


# --- AC-7: asymmetric telemetry nulls excluded from official ----------------
def test_ac7_asymmetric_nulls_excluded(tmp_path):
    ctx = fixed_ctx()
    # primary metric = cost_per_task; control never reports cost ⇒ asymmetric null
    spec, _, ledger = locked_experiment(
        tmp_path / "e", ctx=ctx, primary_metric="cost_per_task",
        decision_rule="delta_cost_per_task < 0",
    )
    for i in range(4):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}", task_id=f"task{i}", arm="control",
                             telemetry={"wall_time_s": 10.0}, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"t-{i}", task_id=f"task{i}", arm="treatment",
                             telemetry={"cost": 1.0, "wall_time_s": 9.0},
                             provenance={"image_digest": "d"})
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    cf = f.comparisons[0]
    assert cf.excluded_from_official is True
    assert "asymmetric" in cf.exclusion_reason
    # the confound flag rides too (disclosure, not suppression)
    assert "telemetry_null_asymmetry" in {c["flag"] for c in f.confounds}
    # official render marks it excluded rather than imputing
    md = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "EXCLUDED" in md


def test_ac7_raw_tokens_never_cross_vendors(tmp_path):
    ctx = fixed_ctx()
    # control anthropic, treatment openai (default fixture) ⇒ cross-vendor
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True,
              control_tel={"tokens_in": 100, "cost": 1.0, "wall_time_s": 10.0},
              treatment_tel={"tokens_in": 50, "cost": 1.0, "wall_time_s": 9.0})
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    sm = f.secondary_metrics
    assert sm["cross_vendor"] is True
    assert "tokens_in" in sm["vendor_incomparable_fields"]
    assert "cost" not in sm["vendor_incomparable_fields"]


# --- one render event (analyze CLI discipline) ------------------------------
def test_analyze_one_render_event(tmp_path):
    from harness.analyze.cli import register
    import typer
    from typer.testing import CliRunner

    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)

    from harness.ledger.query import find_events

    app = typer.Typer()
    register(app)
    before = len(find_events(ledger, "findings_rendered"))
    # single-command Typer app ⇒ invoke without the command name
    result = CliRunner().invoke(app, [str(tmp_path / "e"), "--exploratory"])
    assert result.exit_code == 0, result.output
    after = len(find_events(ledger, "findings_rendered"))
    assert after - before == 1
