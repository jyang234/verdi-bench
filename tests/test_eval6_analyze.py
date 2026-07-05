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
    record_grade,
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
    anchored status the AN-2 fence binds to (not the mutable manifest JSON).

    Also seeds a passing selfcheck: EVAL-1-D008 makes a passed ledgered selfcheck
    an official-render prerequisite, so the official-ready fixtures need one.
    (A refusal test that trips an earlier fence check still refuses — the
    selfcheck check is the fence's last.)"""
    record_calibration_run(
        ledger, ctx, corpus_id=corpus_id, semver=semver, kind="full",
        run={"p": 0.5, "rho": 0.3, "n_tasks": 5}, status="full-run-validated",
    )


def _seed_matching_selfcheck(ledger, ctx, spec, *, n_sim=40, n_boot=500):
    """Seed a passing, current selfcheck [EVAL-1-D008] whose validated CI method
    matches the method ``compute_findings`` will deploy.

    Runs the real selection (same ``spec.seed`` + params) so ``selected_method``
    aligns with the render's, then forces ``passed=True``. Call BEFORE
    ``compute_findings`` and after all data events — the findings are head-bound
    (``_assert_head_hash``), so nothing may be appended between compute and
    render, and the selfcheck event does not affect the delta selection. Pass the
    same ``n_sim``/``n_boot`` the test's ``compute_findings`` uses."""
    from harness.analyze.selfcheck import run_selfcheck
    from harness.ledger.events import record_selfcheck

    res = run_selfcheck(ledger, spec, n_sim=n_sim, n_boot=n_boot)
    res["passed"] = True  # official tests here exercise OTHER gates, not pass/fail
    record_selfcheck(ledger, ctx, **res)


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


def _pref_ledger(tmp_path, ctx, *, arms, **overrides):
    spec, _, ledger = locked_experiment(
        tmp_path / "e", ctx=ctx, arms=arms,
        primary_metric="judge_preference", decision_rule="delta_judge_preference > 0",
        **overrides,
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


def test_an1_attribution_follows_inverted_arm_map(tmp_path):
    """AN-1 owning test: when the recorded arm_map INVERTS the frame
    ({"A": treatment, "B": control} with treatment != arms[0]), the win is
    attributed to the physical arm the map names (treatment), NOT to arms[0].
    Every existing AN-1 test is frame-aligned, so a regression to arms[0] passes
    the rest of the suite — this one catches it."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])  # control, treatment
    inverted = {"A": "treatment", "B": "control"}  # response A is NOT arms[0]
    for i in range(4):
        _seed_pref_verdict(ledger, ctx, cid=f"iv-{i}", task_id=f"t{i}", winner="A", arm_map=inverted)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    by_label = {cf.label: cf for cf in f.comparisons}
    # winner A → treatment (via arm_map), so treatment wins every task and the
    # control-vs-treatment delta (control_rate - treatment_rate) is -1.0, not +1.0.
    assert by_label["control vs treatment"].effect["mean_paired_delta"] == -1.0


def test_m4_multi_arm_default_only_primary_pair_official(tmp_path):
    """PRA-M4: in a 3-arm design, only the primary pair carries an official
    decision by default; the extra pair is exploratory, and the >2-arm
    disclosure is present in both the findings and the render."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS)
    ct = {"A": "control", "B": "treatment"}
    cc = {"A": "control", "B": "challenger"}
    for i in range(4):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
        _seed_pref_verdict(ledger, ctx, cid=f"cc-{i}", task_id=f"t{i}", winner="B", arm_map=cc)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    by_label = {cf.label: cf for cf in f.comparisons}
    assert by_label["control vs treatment"].official_decision is True
    assert by_label["control vs challenger"].official_decision is False
    assert f.multi_arm and f.multi_arm["correction"] == "none"
    assert f.multi_arm["n_arms"] == 3
    md = render_markdown(f, ledger, "exploratory")
    assert "MULTI-ARM" in md and "Exploratory pair" in md


def test_m4_multi_arm_holm_makes_every_pair_official(tmp_path):
    """PRA-M4: a spec-locked multi_arm_correction: holm makes every pair official
    under a Holm-adjusted family, stamping each decision with its Holm p-value
    [F-H7: the policy is pre-registered, not an analyze-time flag]."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS, multi_arm_correction="holm")
    ct = {"A": "control", "B": "treatment"}
    cc = {"A": "control", "B": "challenger"}
    for i in range(4):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
        _seed_pref_verdict(ledger, ctx, cid=f"cc-{i}", task_id=f"t{i}", winner="B", arm_map=cc)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.multi_arm["correction"] == "holm"
    for cf in f.comparisons:
        assert cf.official_decision is True
        assert "holm_p" in cf.decision and cf.decision["correction"] == "holm"
    # deterministic in seed: recomputing yields identical Holm p-values
    f2 = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert [c.decision["holm_p"] for c in f.comparisons] == [
        c.decision["holm_p"] for c in f2.comparisons
    ]


def test_h7_correction_is_read_from_the_locked_spec(tmp_path):
    """F-H7: the multi-arm decision policy rides the sha-locked spec bytes —
    pre-registered, never an analyze-time knob. Absent field ⇒ 'none'."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS, multi_arm_correction="holm")
    ct = {"A": "control", "B": "treatment"}
    cc = {"A": "control", "B": "challenger"}
    for i in range(4):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
        _seed_pref_verdict(ledger, ctx, cid=f"cc-{i}", task_id=f"t{i}", winner="B", arm_map=cc)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.multi_arm["correction"] == "holm"
    for cf in f.comparisons:
        assert cf.official_decision is True and "holm_p" in cf.decision


def test_h7_single_task_pair_is_never_detected(tmp_path):
    """F-H7 floor: one task cluster yields a zero-width CI that excludes zero —
    that is not evidence. detected must be False with the floor named, and the
    render must phrase it as structurally insufficient, not as a null result."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    _seed_pref_verdict(ledger, ctx, cid="c-0", task_id="t0", winner="A",
                       arm_map={"A": "control", "B": "treatment"})
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    cf = f.comparisons[0]
    assert cf.n_tasks == 1
    assert cf.decision["detected"] is False
    assert cf.decision["floor"] == "insufficient_clusters"
    md = render_markdown(f, ledger, "exploratory")
    block = _md_block(md, cf.label)
    assert "Insufficient task clusters" in block
    assert "Effect detected" not in block and "No effect ≥ MDE detected" not in block


def test_h7_holm_single_task_secondary_pair_floored(tmp_path):
    """F-H7: under Holm, a single-task secondary pair reached p≈1/(n_boot+1) and
    was declared an official detected effect — the selfcheck's <2-cluster gate
    only ever inspects the primary pair. The floor binds in the Holm path too."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS, multi_arm_correction="holm")
    ct = {"A": "control", "B": "treatment"}
    cc = {"A": "control", "B": "challenger"}
    for i in range(8):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
    _seed_pref_verdict(ledger, ctx, cid="cc-0", task_id="t0", winner="A", arm_map=cc)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    by_label = {cf.label: cf for cf in f.comparisons}
    sec = by_label["control vs challenger"]
    assert sec.n_tasks == 1
    assert sec.decision["detected"] is False
    assert sec.decision["floor"] == "insufficient_clusters"
    assert sec.decision.get("holm_p") is not None  # p is disclosed; floor binds
    # a 2-cluster pair with a real effect still detects: the floor is minimal
    primary = by_label["control vs treatment"]
    assert primary.n_tasks == 8 and primary.decision["detected"] is True


def test_h7_render_event_records_the_applied_correction(tmp_path):
    """F-H7 defense in depth: every findings_rendered event records the applied
    multi-arm correction, so the chain shows which decision procedure produced
    each render."""
    from harness.analyze.cli import run_analyze
    from harness.ledger.query import find_events

    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS, multi_arm_correction="holm")
    ct = {"A": "control", "B": "treatment"}
    cc = {"A": "control", "B": "challenger"}
    for i in range(3):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
        _seed_pref_verdict(ledger, ctx, cid=f"cc-{i}", task_id=f"t{i}", winner="B", arm_map=cc)
    out = run_analyze(tmp_path / "e", mode="exploratory", actor="tester")
    assert out is not None
    ev = find_events(ledger, "findings_rendered")[-1]
    assert ev["multi_arm_correction"] == "holm"


def test_h7_official_fence_refuses_correction_mismatch(tmp_path):
    """F-H7: an official render whose correction differs from a prior official
    render's recorded correction is refused with its own named reason — one
    pre-registered decision procedure per experiment. Legacy render events
    without the recorded field are skipped, never refused on."""
    import pytest

    from harness.analyze.report import (
        CantAnalyzeReason,
        CorrectionMismatchError,
        _assert_correction_consistent,
        cant_analyze_reason,
    )
    from harness.ledger.events import record_findings_rendered

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    record_findings_rendered(  # legacy official render: no recorded correction
        ledger, ctx, mode="official", primary_metric="judge_preference",
        ledger_head_hash="h" * 64, findings_sha256="f" * 64,
    )
    _assert_correction_consistent("holm", ledger)  # legacy events are skipped
    record_findings_rendered(
        ledger, ctx, mode="official", primary_metric="judge_preference",
        ledger_head_hash="h" * 64, findings_sha256="f" * 64,
        multi_arm_correction="none",
    )
    _assert_correction_consistent("none", ledger)  # matching passes
    with pytest.raises(CorrectionMismatchError, match="one pre-registered decision"):
        _assert_correction_consistent("holm", ledger)
    assert (
        cant_analyze_reason(CorrectionMismatchError("x"))
        is CantAnalyzeReason.correction_mismatch
    )


def test_h7_analyze_flag_is_gone(tmp_path):
    """F-H7: the analyze-time knob is removed — the policy is spec-locked."""
    from typer.testing import CliRunner

    from harness.cli import app

    r = CliRunner().invoke(app, ["analyze", str(tmp_path), "--multi-arm-correction", "holm"])
    assert r.exit_code != 0


def _holm_divergent_findings(tmp_path):
    """A findings document where the primary pair's Holm decision and its raw
    95% CI disagree: 21 wins / 9 losses puts the Holm p under the step-down
    threshold (detected=True) while the deployed CI touches zero. The premise
    is seed-pinned; if the bootstrap stream ever changes legitimately, the
    premise asserts below fail loudly and the fixture needs re-tuning — the
    parity assertion itself must hold for ANY configuration."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS, multi_arm_correction="holm")
    ct = {"A": "control", "B": "treatment"}
    cc = {"A": "control", "B": "challenger"}
    for i in range(30):
        _seed_pref_verdict(
            ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}",
            winner="A" if i < 21 else "B", arm_map=ct,
        )
        _seed_pref_verdict(  # secondary pair: mean-zero, Holm never rejects it
            ledger, ctx, cid=f"cc-{i}", task_id=f"t{i}",
            winner="A" if i % 2 == 0 else "B", arm_map=cc,
        )
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    return f, ledger


def _md_block(md: str, label: str) -> str:
    """The markdown lines for one comparison, up to the next comparison header."""
    start = md.index(f"**Comparison: {label}**")
    rest = md[start + 1:]
    end = rest.find("**Comparison: ")
    return md[start:] if end == -1 else md[start:start + 1 + end]


def test_h6_markdown_verdict_follows_holm_decision(tmp_path):
    """F-H6: markdown must branch on decision['detected'] (the Holm-rewritten,
    dossier-visible decision), not re-derive detection from the unadjusted CI —
    one analyze invocation must never emit two artifacts that disagree."""
    f, ledger = _holm_divergent_findings(tmp_path)
    cf = {c.label: c for c in f.comparisons}["control vs treatment"]
    # premise: the divergence window (see _holm_divergent_findings docstring)
    assert cf.decision["detected"] is True
    assert cf.stats["ci_low"] <= 0.0  # raw CI does NOT exclude zero
    md = render_markdown(f, ledger, "exploratory")
    block = _md_block(md, "control vs treatment")
    assert "Effect detected" in block
    assert "No effect ≥ MDE detected" not in block


def test_h6_dossier_verdict_layer_matches_markdown(tmp_path):
    """F-H6 parity net: the dossier verdict layer and the markdown block state
    the same detection verdict for every comparison under Holm."""
    from harness.analyze.dossier import verdict_sentences

    f, ledger = _holm_divergent_findings(tmp_path)
    md = render_markdown(f, ledger, "exploratory")
    for cf in f.comparisons:
        block = _md_block(md, cf.label)
        sentences = " ".join(verdict_sentences(f, cf))
        md_detected = "Effect detected" in block
        dossier_detected = "an effect was detected" in sentences
        assert md_detected == dossier_detected, cf.label


def test_h6_holm_estimator_split_disclosed_in_both_artifacts(tmp_path):
    """F-H6: under Holm the decision (adjusted recentered-bootstrap p) and the
    displayed interval (unadjusted per-comparison CI) use different procedures —
    both artifacts must say so rather than imply one procedure."""
    from harness.analyze.dossier import verdict_sentences

    f, ledger = _holm_divergent_findings(tmp_path)
    md = render_markdown(f, ledger, "exploratory")
    assert "unadjusted per-comparison" in md
    primary = f.comparisons[0]
    assert any("unadjusted per-comparison" in s for s in verdict_sentences(f, primary))


def test_an10_ci_selection_reports_deployed_n_boot(tmp_path):
    """AN-10 owning test: the coverage selection recorded in the findings uses —
    and reports — the SAME n_boot the deployed interval uses, so the disclosed
    ci_selection cannot silently diverge from the bootstrap that produced the CI."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: i % 2 == 0)
    n_boot = 321  # a non-default value
    f = compute_findings(ledger, spec, spec.seed, coverage_n_sim=20, n_boot=n_boot)
    assert f.ci_selection["n_boot"] == n_boot


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


# --- AN-8 / AN-9: artifact-honest decisions + orphan flagging ---------------
def test_an8_decides_positive_gated_on_detection(tmp_path):
    """AN-8: findings.json's decides_positive is False for a null result (CI
    includes 0), matching the render — not the raw rule fired on an undetected,
    noisy positive delta."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)  # rule delta_holdout_pass_rate > 0
    # control slightly ahead on one noisy task ⇒ observed delta > 0 but CI includes 0
    _populate(ledger, ctx, control_pass=lambda i: i in {0, 1, 2, 4},
              treatment_pass=lambda i: i in {0, 2, 4})
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    cf = f.comparisons[0]
    detected = cf.stats["ci_low"] > 0 or cf.stats["ci_high"] < 0
    assert detected is False
    assert cf.decision["observed_delta"] > 0  # raw rule delta_>_0 WOULD fire
    assert cf.decision["decides_positive"] is False  # but it is gated on detection
    assert cf.decision["detected"] is False


def test_an8_decides_positive_true_when_detected(tmp_path):
    """AN-8: a clean detected effect that meets the rule still decides positive."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: False)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    cf = f.comparisons[0]
    assert cf.decision["detected"] is True
    assert cf.decision["decides_positive"] is True


def test_an9_orphan_grades_flagged(tmp_path):
    """AN-9: a grade with no matching trial record is a ledger inconsistency —
    flagged loudly, not silently dropped (which would shrink n in silence)."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    record_grade(ledger, ctx, trial_id="ghost", task_sha="s", assertions=[], binary_score=True)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.ledger_consistency["n_orphan_grades"] == 1
    assert "ghost" in f.ledger_consistency["orphan_grades"]
    md = render_markdown(f, ledger, "exploratory")
    assert "orphan" in md.lower()


# --- AN-6 / AN-5 / AN-11: claim tags, HTML escaping, ADVISORY surfacing ------
def test_an6_computed_metric_tagged_computed(tmp_path):
    """AN-6: every comparison carries a machine-checkable claim_tag and the render
    shows the marker; a deterministic outcome metric is [computed]."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)  # holdout_pass_rate
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: False)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.comparisons and all(cf.claim_tag == "computed" for cf in f.comparisons)
    assert "[computed]" in render_markdown(f, ledger, "exploratory")


def test_an6_judge_preference_tagged_judgment(tmp_path):
    """AN-6: a judge-preference primary rests on the advisory judge — [judgment]."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    ct = {"A": "control", "B": "treatment"}
    for i in range(3):
        _seed_pref_verdict(ledger, ctx, cid=f"w-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.comparisons and all(cf.claim_tag == "judgment" for cf in f.comparisons)
    assert "[judgment]" in render_markdown(f, ledger, "exploratory")


def test_an5_render_html_escapes_arm_name(tmp_path):
    """AN-5: a <script> in an arm name is escaped in the HTML render, not emitted
    verbatim (the review packet already escapes; render_html did not)."""
    ctx = fixed_ctx()
    evil = "ctl<script>alert(1)</script>"
    arms = [
        {"name": evil, "platform": "claude_code",
         "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
        {"name": "treatment", "platform": "codex", "model": "openai/gpt-4o-2024-08-06", "payload": {}},
    ]
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx, arms=arms)
    for i in range(3):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c{i}", task_id=f"t{i}", arm=evil,
                             passed=True, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"x{i}", task_id=f"t{i}", arm="treatment",
                             passed=False, provenance={"image_digest": "d"})
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    html_out = render_html(f, ledger, "exploratory")
    assert "<script>alert(1)</script>" not in html_out  # not emitted verbatim
    assert "&lt;script&gt;" in html_out                  # escaped


def test_coverage_insufficient_echoes_ci_level():
    """With <2 realized clusters, coverage selection is 'insufficient' and reports
    the REQUESTED ci_level as nominal (not a hardcoded 0.95), so the disclosed
    nominal agrees with the level the percentile fallback deploys."""
    from harness.analyze.nullsim import NULL_CONTINUOUS, coverage_from_deltas

    sel = coverage_from_deltas([0.3], seed=1, null_model=NULL_CONTINUOUS, ci_level=0.90)
    assert sel.null_model == "insufficient_data"
    assert sel.selected_method == "percentile"
    assert sel.nominal == 0.90


def test_an11_advisory_tier_surfaced(tmp_path):
    """AN-11: local/fake results are ADVISORY-tier; the render surfaces the tier so
    'Local = ADVISORY' is honestly reflected, not silently stamped."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.tier["advisory"] is True
    assert "ADVISORY" in render_markdown(f, ledger, "exploratory")


def test_7b3_grader_stamp_local_banners_over_trusted_trials(tmp_path):
    """7B-3: an explicit --runner local grade over trusted-tier trials must
    banner ADVISORY. _tier_summary read only trial provenance (the write-only
    grader-stamp hole); the grade-level grader stamp is now authoritative."""
    from harness.adapters.base import ADVISORY, Outcome, Provenance, Telemetry, TrialRecord
    from harness.analyze.report import _tier_summary
    from harness.ledger.events import record_grade, record_trial

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    rec = TrialRecord.assemble(
        trial_id="tr", task_id="t0", arm="control", repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(),
        provenance=Provenance(tier="TRUSTED"), artifacts_path="/tmp/tr/artifacts",
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    # trial provenance alone is trusted → no ADVISORY yet
    assert _tier_summary(ledger)["advisory"] is False
    # a local (advisory) grade over the trusted trial flips the banner on
    record_grade(ledger, ctx, trial_id="tr", task_sha="s",
                 assertions=[{"id": "h1", "source": "holdout_test", "result": "pass"}],
                 binary_score=True, grader="local")
    summary = _tier_summary(ledger)
    assert summary["advisory"] is True
    assert ADVISORY in summary["tiers"]
    # an absent grader field (pre-stamp ledger) must add no new signal
    ledger2 = tmp_path / "l2.ndjson"
    record_trial(ledger2, ctx, trial_record=rec.model_dump(mode="json"))
    record_grade(ledger2, ctx, trial_id="tr", task_sha="s",
                 assertions=[{"id": "h1", "source": "holdout_test", "result": "pass"}],
                 binary_score=True)  # no grader kwarg
    assert _tier_summary(ledger2)["advisory"] is False


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
    _seed_matching_selfcheck(ledger, ctx, spec)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    md = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "Official findings" in md
    assert "EXPLORATORY" not in md  # official carries no watermark
    assert spec.primary_metric.value in md


def test_dp7_2_override_disclosure_in_official_render(tmp_path):
    """D-P7-2 refinement (b): the terminal-override disclosure rides the
    OFFICIAL render too, not only the exploratory one (which the retry-terminal
    e2e test owns) — a manual override must never be invisible in the official
    findings."""
    from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
    from harness.ledger.events import record_cant_grade, record_grade, record_trial
    from harness.ledger.query import event_line_hash, find_events

    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)

    # Production override shape [D-P7-2]: an extra trial whose first grade
    # attempt was a terminal cant_grade, then the --retry-terminal re-attempt's
    # grade stamped with the overridden line's hash.
    rec = TrialRecord.assemble(
        trial_id="c-0-2", task_id="task0", arm="control", repetition=2,
        outcome=Outcome.completed, telemetry=Telemetry(cost=1.0, wall_time_s=10.0),
        provenance=Provenance(image_digest="digestC"), flags=Flags(),
        artifacts_path="/tmp/c-0-2/artifacts",
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    record_cant_grade(ledger, ctx, trial_id="c-0-2", reason="container_failure")
    overridden = find_events(ledger, "cant_grade")[-1]
    record_grade(
        ledger, ctx, trial_id="c-0-2", task_sha="sha-task0",
        assertions=[{"id": "h1", "source": "holdout_test", "result": "pass"}],
        binary_score=True, override_of=event_line_hash(overridden),
    )

    _seed_full_calibration(ledger, ctx)
    _seed_matching_selfcheck(ledger, ctx, spec)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    assert f.overrides == {"n_override_events": 1, "override_trials": ["c-0-2"]}
    official = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "Official findings" in official
    assert "Terminal overrides" in official
    assert "1 override-graded re-attempt(s)" in official and "c-0-2" in official


def test_d002_identity_blind_disclosure_in_both_renders(tmp_path):
    """D-1/D002: both renders carry the [computed] disclosure that the judge is
    identity-blind (not outcome-blind) — judge_preference is not independent of
    holdout_pass_rate because the packet includes holdout results by design."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    _seed_full_calibration(ledger, ctx)
    _seed_matching_selfcheck(ledger, ctx, spec)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    for md in (
        render_markdown(f, ledger, "exploratory"),
        render_markdown(f, ledger, "official", corpus_manifest=_full_corpus()),
    ):
        assert "identity-blind" in md
        assert "not independent of holdout_pass_rate" in md
        assert "[computed]" in md


def test_cant_analyze_reason_maps_phase7_fence_errors():
    """The Phase-7 fence refusals (rubric swap, missing/failed selfcheck) each get
    their own distinguishable cant_analyze reason — not the generic analyze_error
    fallback that would erase which gate refused [AN-3 closed set]."""
    from harness.analyze.report import (
        CantAnalyzeReason,
        RubricMismatchError,
        SelfcheckRequiredError,
        cant_analyze_reason,
    )

    assert cant_analyze_reason(RubricMismatchError("x")) == CantAnalyzeReason.rubric_mismatch
    assert cant_analyze_reason(SelfcheckRequiredError("x")) == CantAnalyzeReason.selfcheck_required


def test_dp7_6_official_fence_refuses_rubric_mismatch(tmp_path):
    """D-P7-6: when the lock committed a rubric_sha256, a verdict whose provenance
    rubric hash disagrees refuses the official render (post-lock rubric swap)."""
    from harness.analyze.report import RubricMismatchError
    from harness.judge.schema import Verdict, VerdictProvenance, Winner
    from harness.ledger.events import append_verdict

    from harness.ledger.query import find_events

    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    lock = find_events(ledger, "experiment_locked")[0]
    assert lock.get("rubric_sha256")  # lock committed a rubric hash
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: False)
    _seed_full_calibration(ledger, ctx)
    prov = VerdictProvenance(
        judge_model="fake/x", rubric_sha256="deadbeef" * 8, packet_sha256="p",
        call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t",
    )
    v = Verdict(winner=Winner("A"), reason="r",
                evidence=[Evidence(kind="diff", response="A", hunk="h")], provenance=prov,
                source="judge", comparison_id="cmp-task0-r0", task_class="refactor")
    append_verdict(ledger, ctx, verdict=v.model_dump(mode="json"))

    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    with pytest.raises(RubricMismatchError):
        render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())


def test_dp7_6_legacy_lock_official_caveat(tmp_path):
    """A legacy lock (no committed rubric hash) is not refused — the official
    render carries a caveat that the rubric content is not pinned [D-P7-6]."""
    from harness.corpus.commit import compute_commitment, load_task_dicts
    from harness.ledger.events import record_experiment_locked
    from harness.plan.lock import spec_sha256
    from harness.plan.power import AssumedVariance, mde_check
    from harness.schema.experiment import ExperimentSpec
    from tests.fixtures.builders import write_experiment_yaml

    ctx = fixed_ctx()
    exp_dir = tmp_path / "e"
    exp_dir.mkdir()
    spec_path = write_experiment_yaml(exp_dir / "experiment.yaml")
    ledger = exp_dir / "ledger.ndjson"
    spec = ExperimentSpec.from_yaml(spec_path)
    mde = mde_check(spec, AssumedVariance(), n_sim=8, n_boot=40, deltas=[0.2, 0.4])
    # a LEGACY lock — no rubric_sha256 field
    record_experiment_locked(
        ledger, ctx, spec_sha256=spec_sha256(spec_path), spec_path=str(spec_path),
        seed=spec.seed, mde=mde, attested_by="t", method="m",
    )
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: False)
    _seed_full_calibration(ledger, ctx)
    _seed_matching_selfcheck(ledger, ctx, spec)
    f = compute_findings(ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST)
    assert f.rubric_committed is False
    md = render_markdown(f, ledger, "official", corpus_manifest=_full_corpus())
    assert "CAVEAT" in md and "rubric" in md.lower()


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
    # AN-6: every comparison claim carries a machine-checkable [computed]/[judgment]
    # tag, and the render surfaces it — provenance is not just fields, claims are typed
    assert f.comparisons and all(
        cf.claim_tag in ("computed", "judgment") for cf in f.comparisons
    )
    assert f"[{f.comparisons[0].claim_tag}]" in render_markdown(f, ledger, "exploratory")
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
    _seed_matching_selfcheck(ledger, ctx, spec)
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
    # register() now attaches both `analyze` and `selfcheck`, so name the command
    result = CliRunner().invoke(app, ["analyze", str(tmp_path / "e"), "--exploratory"])
    assert result.exit_code == 0, result.output
    after = len(find_events(ledger, "findings_rendered"))
    assert after - before == 1


def test_m_j1_terminal_cant_judge_exclusions_are_disclosed(tmp_path):
    """F-M-J1: terminal CANT_JUDGE comparisons are permanently excluded from
    judge_preference (excluded-never-imputed) — previously with zero disclosure,
    a biased missing-data channel an arm can drive (canary salting, junk-file
    context overflow). The counts now ride the findings and both renders."""
    from harness.analyze.dossier import _disclosure_sections

    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    ct = {"A": "control", "B": "treatment"}
    for i in range(4):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
    for i, reason in enumerate(("identity_leak", "identity_leak", "context_overflow")):
        prov = VerdictProvenance(
            judge_model="fake/judge-1", rubric_sha256="r" * 64, packet_sha256="p" * 64,
            call_ids=["c1"], orders="both", temperature=0.0, ts="t",
        )
        v = Verdict(
            winner=Winner("CANT_JUDGE"), reason=reason, evidence=[], provenance=prov,
            comparison_id=f"cj-{i}", task_id=f"x{i}", task_class="cls", arm_map=ct,
        )
        append_verdict(ledger, ctx, verdict=v.model_dump(mode="json"))

    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.judge_coverage["verdicts"] == 7
    assert f.judge_coverage["cant_judge"] == {"context_overflow": 1, "identity_leak": 2}
    assert f.judge_coverage["terminal_cant_judge"] == 3

    md = render_markdown(f, ledger, "exploratory")
    assert "Judge coverage" in md and "identity_leak: 2" in md
    titles = [sec["title"] for sec in _disclosure_sections(f)]
    assert "Judge coverage" in titles  # dossier parity


def test_m_j1_full_judge_coverage_discloses_nothing(tmp_path):
    """No CANT_JUDGE ⇒ no coverage section — disclosure is for exclusions."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    ct = {"A": "control", "B": "treatment"}
    for i in range(3):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.judge_coverage["cant_judge"] == {}
    assert "Judge coverage" not in render_markdown(f, ledger, "exploratory")


def test_m_s3_achieved_mde_reconciled_to_realized_n(tmp_path):
    """F-M-S3: the null phrasing previously interpolated the plan-time MDE even
    when the realized cluster count fell below plan — overstating sensitivity.
    When realized N < plan N, the findings carry a disclosed 1/√n-scaled
    achieved MDE and the renders use it; at plan N the plan figure stands."""
    from harness.analyze.report import display_mde
    from harness.ledger.query import find_events

    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    plan_n = find_events(ledger, "experiment_locked")[0]["mde"]["n_tasks"]
    ct = {"A": "control", "B": "treatment"}
    realized = 3
    assert realized < plan_n  # the premise: fewer clusters than the plan assumed
    for i in range(realized):  # a mean-zero pattern: the null phrasing renders
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}",
                           winner="A" if i % 2 == 0 else "B", arm_map=ct)
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    mde = f.mde
    assert mde.realized_n_tasks == realized
    assert mde.achieved_value is not None and mde.achieved_value > mde.value
    expected = round(mde.value * (plan_n / realized) ** 0.5, 4)
    assert abs(mde.achieved_value - expected) < 1e-9
    assert display_mde(mde) == mde.achieved_value

    md = render_markdown(f, ledger, "exploratory")
    assert "achieved at realized n_tasks=3" in md
    # the structural-null line quotes the achieved figure, not the plan figure
    block = _md_block(md, f.comparisons[0].label)
    if "No effect ≥ MDE detected" in block:
        assert f"MDE={mde.achieved_value:.4f}" in block


def test_m_j2_identity_leak_rate_disclosed_per_task_class(tmp_path):
    """F-M-J2: identity_leak counts are surfaced per task class, so a scrub
    pattern over-broad for one class (a corpus-wide FP pattern) is visible as a
    concentrated rate rather than silently biasing judge_preference."""
    ctx = fixed_ctx()
    spec, ledger = _pref_ledger(tmp_path, ctx, arms=_PREF_ARMS[:2])
    ct = {"A": "control", "B": "treatment"}
    for i in range(3):
        _seed_pref_verdict(ledger, ctx, cid=f"ct-{i}", task_id=f"t{i}", winner="A", arm_map=ct)
    # two identity_leak CANT_JUDGE verdicts in the "google_api" class, one in "refactor"
    for i, cls in enumerate(("google_api", "google_api", "refactor")):
        prov = VerdictProvenance(
            judge_model="fake/judge-1", rubric_sha256="r" * 64, packet_sha256="p" * 64,
            call_ids=["c1"], orders="both", temperature=0.0, ts="t",
        )
        v = Verdict(
            winner=Winner("CANT_JUDGE"), reason="identity_leak", evidence=[], provenance=prov,
            comparison_id=f"cj-{i}", task_id=f"x{i}", task_class=cls, arm_map=ct,
        )
        append_verdict(ledger, ctx, verdict=v.model_dump(mode="json"))

    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.judge_coverage["identity_leak_by_class"] == {"google_api": 2, "refactor": 1}
    md = render_markdown(f, ledger, "exploratory")
    assert "identity_leak by task class" in md and "google_api: 2" in md
