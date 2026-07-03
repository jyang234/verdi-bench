"""EVAL-6 — analyze: paired stats, effect sizes, confounds, the fence, provenance."""

from __future__ import annotations

import pytest

from harness.analyze.confounds import flag_confounds
from harness.analyze.effect import cliffs_delta, effect_sizes
from harness.analyze.report import (
    CalibrationIncompleteError,
    CorpusMismatchError,
    ProvenanceError,
    UnregisteredOfficialError,
    compute_findings,
    render_html,
    render_markdown,
)
from harness.analyze.stats import paired_bootstrap
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.judge.schema import Evidence, Verdict, VerdictProvenance, Winner
from harness.ledger.events import (
    append_verdict,
    record_calibration_run,
    record_executed_order,
    record_trial_infra_failed,
)
from harness.ledger.query import ledger_head_hash, verify
from tests.fixtures.builders import (
    fixed_ctx,
    locked_experiment,
    seed_trial_and_grade,
)

_FAST = dict(coverage_n_sim=40, n_boot=500)


def _full_corpus():
    # AN-2: the fence binds the cited manifest to the pre-registered spec corpus
    # (public-mini@1.0.0) and to the tasks the experiment ran (task0..task4), so
    # the manifest must match both — the old terminal-bench@2.0.0 / one-task
    # manifest was the mismatch the shipped tests baked in.
    m = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha="a" * 64, status="admitted") for i in range(5)],
    )
    # status is kept for provenance, but the FENCE now reads the ledgered
    # calibration_run events (CO-4), so official tests must _seed_full_calibration.
    m.calibration.status = "full-run-validated"
    return m


def _seed_full_calibration(ledger, ctx, *, corpus_id="public-mini", semver="1.0.0"):
    """Ledger a full-run-validated calibration_run for the corpus — the chain-
    anchored status the AN-2 fence binds to (not the mutable manifest JSON)."""
    record_calibration_run(
        ledger, ctx, corpus_id=corpus_id, semver=semver, kind="full",
        run={"p": 0.5, "rho": 0.3, "n_tasks": 5}, status="full-run-validated",
    )


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


# --- AN-4: coverage at the realized N with a metric-appropriate null ---------
def test_an4_continuous_metric_uses_continuous_null(tmp_path):
    """AN-4: a continuous primary (cost) selects its CI method under a *continuous*
    null at the realized paired-task count — not the old paired-binary null at the
    assumed n_tasks=50. The null model used is recorded for disclosure."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(
        tmp_path / "e", ctx=ctx, primary_metric="cost_per_task",
        decision_rule="delta_cost_per_task < 0",
    )
    for i in range(6):  # BOTH arms report cost ⇒ not excluded, 6 paired tasks
        seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}", task_id=f"task{i}", arm="control",
                             telemetry={"cost": 1.0 + 0.1 * i, "wall_time_s": 10.0},
                             provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"t-{i}", task_id=f"task{i}", arm="treatment",
                             telemetry={"cost": 1.1 + 0.1 * i, "wall_time_s": 9.0},
                             provenance={"image_digest": "d"})
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    assert f.ci_selection["null_model"] == "paired_continuous"
    assert f.ci_selection["n_tasks"] == 6  # realized paired-task count, not 50


def test_an4_binary_metric_uses_binary_null_at_realized_n(tmp_path):
    """AN-4: a binary primary (holdout) uses a paired-binary null, again at the
    realized paired-task count."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)  # holdout_pass_rate
    _populate(ledger, ctx, control_pass=lambda i: i % 2 == 0, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    assert f.ci_selection["null_model"] == "paired_binary"
    assert f.ci_selection["n_tasks"] == 5  # _populate seeds 5 paired tasks


# --- AN-1 / AN-7: judge-preference filtered by arm pair, clustered, not imputed -
_PREF_ARMS = [
    {"name": "control", "platform": "claude_code",
     "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
    {"name": "treatment", "platform": "codex", "model": "openai/gpt-4o-2024-08-06", "payload": {}},
    {"name": "challenger", "platform": "codex",
     "model": "openai/gpt-4o-mini-2024-07-18", "payload": {}},
]


def _pref_ledger(tmp_path, ctx, *, arms):
    spec, _, ledger = locked_experiment(
        tmp_path / "e", ctx=ctx, arms=arms,
        primary_metric="judge_preference", decision_rule="delta_judge_preference > 0",
    )
    return spec, ledger


def _seed_pref_verdict(ledger, ctx, *, cid, task_id, winner, arm_map):
    prov = VerdictProvenance(
        judge_model="fake/judge-1", rubric_sha256="r" * 64, packet_sha256="p" * 64,
        call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t",
    )
    ev = (
        [] if winner in ("TIE", "CANT_JUDGE")
        else [Evidence(kind="diff", response=winner, hunk="@@")]
    )
    v = Verdict(
        winner=Winner(winner), reason="x", evidence=ev, provenance=prov,
        comparison_id=cid, task_id=task_id, task_class="cls", arm_map=arm_map,
    )
    append_verdict(ledger, ctx, verdict=v.model_dump(mode="json"))


def test_an1_judge_preference_filtered_by_arm_pair(tmp_path):
    """AN-1: judge-preference deltas are filtered by arm pair via the recorded
    arm_map and attributed to the physical arm — the same pooled verdicts no
    longer feed every comparison. control beats treatment but loses to challenger,
    so the two comparisons must report OPPOSITE signs, not one pooled 0.0."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS)
    ct = {"A": "control", "B": "treatment"}
    cc = {"A": "control", "B": "challenger"}
    for i in range(4):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
        _seed_pref_verdict(ledger, ctx, cid=f"cc-{i}", task_id=f"t{i}", winner="B", arm_map=cc)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    by_label = {cf.label: cf for cf in f.comparisons}
    assert by_label["control vs treatment"].effect["mean_paired_delta"] == 1.0
    assert by_label["control vs challenger"].effect["mean_paired_delta"] == -1.0


def test_an1_cant_judge_and_tie_excluded_not_imputed(tmp_path):
    """AN-1: CANT_JUDGE and TIE are non-answers — excluded from the preference
    series, never imputed as 0.0. n reflects real A/B verdicts only."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    ct = {"A": "control", "B": "treatment"}
    for i in range(3):
        _seed_pref_verdict(ledger, ctx, cid=f"w-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
    _seed_pref_verdict(ledger, ctx, cid="tie", task_id="t3", winner="TIE", arm_map=ct)
    _seed_pref_verdict(ledger, ctx, cid="cant", task_id="t4", winner="CANT_JUDGE", arm_map=ct)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    cf = f.comparisons[0]
    assert cf.n_tasks == 3  # only the real A/B tasks, not 5
    assert cf.effect["mean_paired_delta"] == 1.0  # not diluted to 0.6 by imputed 0s


def test_an1_per_task_winrate_clusters_reps(tmp_path):
    """AN-1: multiple verdicts for one task reduce to that task's win-rate (the
    cluster), so the bootstrap resamples tasks, not individual verdicts."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    ct = {"A": "control", "B": "treatment"}
    _seed_pref_verdict(ledger, ctx, cid="a0", task_id="t0", winner="A", arm_map=ct)
    _seed_pref_verdict(ledger, ctx, cid="a1", task_id="t0", winner="A", arm_map=ct)  # t0 winrate 1.0
    _seed_pref_verdict(ledger, ctx, cid="b0", task_id="t1", winner="A", arm_map=ct)
    _seed_pref_verdict(ledger, ctx, cid="b1", task_id="t1", winner="B", arm_map=ct)  # t1 winrate 0.5
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    cf = f.comparisons[0]
    assert cf.n_tasks == 2  # two task clusters, not four verdicts
    assert cf.effect["mean_paired_delta"] == 0.5  # mean of per-task deltas (+1, 0)


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
    # official render against a corpus that is not the pre-registered one is
    # refused (AN-2: corpus "c" ≠ the spec's public-mini)
    with pytest.raises(CorpusMismatchError):
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


def test_pl14_legacy_ack_event_still_surfaced(tmp_path):
    # PL-14 backward-compat: a ledger locked before the ack was folded inline
    # recorded a separate (now-retired) acknowledged_underpowered event; analyze
    # must still surface the acknowledgment for such legacy ledgers.
    from harness.analyze.report import _mde_block
    from harness.ledger.chain import append_event

    ctx = fixed_ctx()
    _, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)  # powered ⇒ no inline ack
    assert _mde_block(ledger).acknowledged_underpowered is False
    append_event(ledger, {
        "event": "acknowledged_underpowered",
        "provenance": {"ts": "t", "actor": "a", "experiment_id": "e",
                       "instrument": {"version": "0", "git_sha": "0"}},
        "mde": None, "hypothesized_effect": 0.001,
    })
    assert _mde_block(ledger).acknowledged_underpowered is True


def test_an3_bad_corpus_manifest_fails_closed(tmp_path):
    # AN-3: a malformed --corpus must fail closed to one cant_analyze event, not
    # escape the CLI with zero events (the load was moved inside the envelope).
    from typer.testing import CliRunner

    from harness.cli import app
    from harness.ledger.query import find_events

    ctx = fixed_ctx()
    _, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    exp_dir = tmp_path / "e"
    bad = exp_dir / "bad_corpus.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    result = CliRunner().invoke(app, ["analyze", str(exp_dir), "--official", "--corpus", str(bad)])
    assert result.exit_code == 2
    cant = find_events(ledger, "cant_analyze")
    assert len(cant) == 1
    assert not (exp_dir / "findings.json").exists()


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
    _seed_full_calibration(ledger, ctx)  # AN-2: ledgered full-run-validated
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    md = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "Official findings" in md
    assert "EXPLORATORY" not in md  # official carries no watermark
    assert spec.primary_metric.value in md


# --- AN-2: official fence bound to corpus identity --------------------------
def test_an2_official_fence_refuses_mismatched_corpus(tmp_path):
    """AN-2: a corpus that is not the pre-registered one is refused for official —
    even when it claims full-run-validated in its JSON. Reproduces the accepted
    TOTALLY-DIFFERENT-CORPUS bypass."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)  # spec corpus public-mini@1.0.0
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    _seed_full_calibration(ledger, ctx)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    wrong = CorpusManifest(
        corpus_id="totally-different", semver="9.9.9", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha="a" * 64, status="admitted") for i in range(5)],
    )
    wrong.calibration.status = "full-run-validated"
    with pytest.raises(CorpusMismatchError):
        render_markdown(f, ledger, "official", corpus_manifest=wrong)


def test_an2_official_fence_refuses_unledgered_status(tmp_path):
    """AN-2/CO-4: the right corpus but only a hand-edited manifest status (no
    ledgered calibration_run) is refused — the JSON status is not trusted."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    with pytest.raises(CalibrationIncompleteError):  # _full_corpus status set in memory only
        render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())


def test_an2_official_fence_refuses_uncovered_task(tmp_path):
    """AN-2: a manifest that omits a task the experiment ran does not cover the
    data and is refused, even with the right id/semver and a ledgered status."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)  # task0..4
    _seed_full_calibration(ledger, ctx)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    short = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha="a" * 64, status="admitted") for i in range(3)],
    )
    with pytest.raises(CorpusMismatchError):  # task3, task4 not admitted in `short`
        render_markdown(f, ledger, "official", corpus_manifest=short)


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
    assert p.corpus["corpus_id"] == "public-mini"
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
    _seed_full_calibration(ledger, ctx)  # AN-2: ledgered full-run-validated
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
