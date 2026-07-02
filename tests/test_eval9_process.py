"""EVAL-9 — process rubric: versioning, isolation, firewalls, calibration, rendering."""

from __future__ import annotations

import inspect
import json

import pytest
from pydantic import ValidationError

from harness.judge.providers.fake import FakeProvider
from harness.ledger.events import EventContext
from harness.ledger.query import find_events
from harness.process.calibrate import (
    process_kappa_by_dimension,
    score_telemetry_correlation,
)
from harness.process.packet import RedactionLeakError, build_process_packet
from harness.process.rubric import Dimension, ProcessRubric, default_rubric
from harness.process.score import (
    DimensionScore,
    ProcessScore,
    ProcessScoreProvenance,
    ProcessSequencingError,
    Scorer,
    score_trial_process,
    record_human_process_score,
)
from harness.review.kappa import ReviewedItem
from harness.schema.errors import CompositePrimaryMetricError
from harness.schema.experiment import ExperimentSpec
from harness.schema.metrics import PrimaryMetric
from tests.fixtures.builders import fixed_ctx, valid_experiment_dict


def _rubric():
    return default_rubric()


# --- AC-1: rubric versioned + ordinal schema --------------------------------
def test_ac1_rubric_versioned():
    r = _rubric()
    assert r.rubric_version == "process-v1"
    # the five v1 dimensions [D003]
    assert r.dimension_ids == [
        "planning_quality", "exploration_efficiency", "error_recovery",
        "instruction_adherence", "destructive_action_caution",
    ]


def test_ac1_ordinal_schema():
    r = _rubric()
    for d in r.dimensions:
        assert d.scale == 5
        assert set(d.anchors) == {1, 2, 3, 4, 5}  # anchors cover the full scale
    # a dimension missing an anchor is rejected
    with pytest.raises(ValidationError):
        Dimension(id="x", name="X", scale=5, anchors={1: "a", 2: "b"},
                  telemetry_correlates=["tokens"])
    # an unknown telemetry correlate is rejected
    with pytest.raises(ValidationError):
        Dimension(id="x", name="X", scale=5,
                  anchors={i: str(i) for i in range(1, 6)},
                  telemetry_correlates=["gpu_hours"])


def test_ac1_rubric_version_stamped(tmp_path):
    # every process_score event carries the rubric version
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    r = _rubric()
    fp = FakeProvider([json.dumps({"scores": {d: 3 for d in r.dimension_ids}})])
    score_trial_process("t1", "clean transcript", r, ledger_path=ledger, ctx=ctx, ts="t",
                        scorer_id="judge", provider=fp)
    ev = find_events(ledger, "process_score")[0]
    assert ev["process_score"]["rubric_version"] == "process-v1"


# --- AC-2: unblinded provenance + disclosure required -----------------------
def test_ac2_unblinded_provenance():
    # unblinded is pinned True ⇒ a score without unblinded provenance is unrepresentable
    with pytest.raises(ValidationError):
        ProcessScoreProvenance(unblinded=False, scorer=Scorer(kind="judge", id="j"),
                               judge_vendor_overlap=False, ts="t")
    prov = ProcessScoreProvenance(unblinded=True, scorer=Scorer(kind="judge", id="j"),
                                  judge_vendor_overlap=False, ts="t")
    assert prov.unblinded is True


def test_ac2_disclosure_required(tmp_path):
    from harness.analyze.report import DisclosureError, render_markdown
    from tests.fixtures.builders import locked_experiment, seed_trial_and_grade
    from harness.analyze.report import compute_findings

    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    for i in range(3):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c{i}", task_id=f"t{i}", arm="control",
                             passed=True, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"x{i}", task_id=f"t{i}", arm="treatment",
                             passed=True, provenance={"image_digest": "d"})
    r = _rubric()
    fp = FakeProvider([json.dumps({"scores": {d: 4 for d in r.dimension_ids}})] * 6)
    score_trial_process("c0", "clean", r, ledger_path=ledger, ctx=ctx, ts="t",
                        scorer_id="judge", provider=fp)
    findings = compute_findings(ledger, spec, spec.seed, coverage_n_sim=20, coverage_n_boot=60,
                                n_boot=200)
    assert findings.process is not None
    assert findings.process["disclosure"]["unblinded"] is True
    # strip the disclosure ⇒ render must refuse
    findings.process["disclosure"] = None
    with pytest.raises(DisclosureError):
        render_markdown(findings, ledger, "exploratory")


# --- AC-3: firewalls (isolation + post-reveal only) -------------------------
def test_ac3_judge_call_isolated():
    # the packet builder's signature IS the allowlist: no verdict/outcome param
    params = set(inspect.signature(build_process_packet).parameters)
    assert params == {"transcript", "rubric", "telemetry"}
    for forbidden in ("verdict", "winner", "judge_verdict", "outcome"):
        assert forbidden not in params
    # and the judge-facing render carries no verdict content
    pkt = build_process_packet("agent transcript here", _rubric())
    body = pkt.render_judge()[1]["content"]
    assert "winner" not in body.lower()


def test_ac3_human_post_reveal_only(tmp_path):
    from harness.ledger.events import record_reveal

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    r = _rubric()
    scores = [DimensionScore(dim_id=d.id, score=4) for d in r.dimensions]
    # before the reveal exists, human process scoring is refused
    with pytest.raises(ProcessSequencingError):
        record_human_process_score("t1", r, scores, ledger_path=ledger, ctx=ctx, ts="t",
                                   scorer_id="human", comparison_id="cmp-1")
    # after the reveal for that comparison, it is allowed
    record_reveal(ledger, ctx, verdict_event_id="cmp-1",
                  revealed={"judge_verdict_id": "j", "arm_identities": {}})
    ps = record_human_process_score("t1", r, scores, ledger_path=ledger, ctx=ctx, ts="t",
                                    scorer_id="human", comparison_id="cmp-1")
    assert ps.provenance.scorer.kind == "human"


# --- AC-4: full-or-CANT_SCORE + redaction upstream --------------------------
def test_ac4_full_or_cant_score(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    r = _rubric()
    fp = FakeProvider([json.dumps({"scores": {d: 3 for d in r.dimension_ids}})])
    # an over-context transcript fails closed to CANT_SCORE(context_overflow) with tokens
    ps = score_trial_process("t1", "x" * 4000, r, ledger_path=ledger, ctx=ctx, ts="t",
                             scorer_id="judge", provider=fp, max_context_tokens=10)
    assert all(s.is_cant_score for s in ps.scores)
    assert all(s.cant_score_reason == "context_overflow" for s in ps.scores)
    assert all(s.tokens is not None for s in ps.scores)  # token counts recorded [AC-4]
    # exactly one event appended (fail-closed still ledgers)
    assert len(find_events(ledger, "process_score")) == 1


def test_ac4_redaction_upstream():
    # a secret canary (shared corpus) must never reach the scorer payload
    with pytest.raises(RedactionLeakError):
        build_process_packet("token AKIA" + "1234567890123456", _rubric())
    # a clean, post-redaction transcript is accepted
    build_process_packet("clean transcript, redacted", _rubric())


def test_ac4_provider_error_cant_score(tmp_path):
    from harness.judge.providers.base import ProviderError

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    r = _rubric()
    fp = FakeProvider([ProviderError("boom")])
    ps = score_trial_process("t1", "clean", r, ledger_path=ledger, ctx=ctx, ts="t",
                             scorer_id="judge", provider=fp)
    assert all(s.cant_score_reason == "provider_error" for s in ps.scores)


# --- AC-5: weighted kappa + per-dimension gates -----------------------------
def test_ac5_weighted_kappa():
    # quadratic-weighted kappa hand-check (see calibrate/kappa derivation): 2/3
    items = {
        "planning_quality": [
            ReviewedItem(1, 1, "mandatory"), ReviewedItem(1, 2, "mandatory"),
            ReviewedItem(3, 2, "mandatory"), ReviewedItem(3, 3, "mandatory"),
        ]
    }
    cal = process_kappa_by_dimension(items, kappa_threshold=0.6)
    assert abs(cal["planning_quality"].kappa - 2 / 3) < 1e-9
    assert cal["planning_quality"].escalate is False  # 0.667 >= 0.6


def test_ac5_per_dimension_gates():
    # one dimension below threshold escalates without dragging the other
    good = [ReviewedItem(s, s, "floor") for s in [1, 2, 3, 4, 5]]  # perfect ⇒ kappa 1
    bad = [ReviewedItem(1, 5, "mandatory"), ReviewedItem(5, 1, "mandatory"),
           ReviewedItem(1, 5, "mandatory"), ReviewedItem(5, 1, "mandatory")]  # anti ⇒ low
    cal = process_kappa_by_dimension(
        {"good_dim": good, "bad_dim": bad}, kappa_threshold=0.6
    )
    assert cal["good_dim"].escalate is False
    assert cal["bad_dim"].escalate is True
    # gates are independent — good_dim is unaffected by bad_dim
    assert cal["good_dim"].kappa == 1.0


# --- AC-6: metric firewall + exploratory rendering --------------------------
def test_ac6_primary_ineligible():
    # process dimension ids are not members of the closed PrimaryMetric enum
    for dim in default_rubric().dimension_ids:
        assert dim not in PrimaryMetric.values()
    # registering one as primary_metric fails schema by construction
    with pytest.raises(CompositePrimaryMetricError):
        ExperimentSpec.from_dict(valid_experiment_dict(primary_metric="planning_quality"))


def test_ac6_exploratory_rendering(tmp_path):
    from harness.analyze.report import compute_findings, render_markdown
    from tests.fixtures.builders import locked_experiment, seed_trial_and_grade

    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    for i in range(3):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c{i}", task_id=f"t{i}", arm="control",
                             passed=True, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"x{i}", task_id=f"t{i}", arm="treatment",
                             passed=True, provenance={"image_digest": "d"})
    r = _rubric()
    fp = FakeProvider([json.dumps({"scores": {d: 4 for d in r.dimension_ids}})])
    score_trial_process("c0", "clean", r, ledger_path=ledger, ctx=ctx, ts="t",
                        scorer_id="judge", provider=fp)
    findings = compute_findings(ledger, spec, spec.seed, corpus_manifest=None,
                                coverage_n_sim=20, coverage_n_boot=60, n_boot=200)
    # exploratory render shows process as a labeled, disclosed secondary
    md = render_markdown(findings, ledger, "exploratory")
    assert "Process diagnostics (EXPLORATORY secondary)" in md
    assert "UNBLINDED DIAGNOSTIC" in md
    assert "planning_quality" in md
    # official-path exclusion: process never appears in an official render
    from harness.corpus.registry import CorpusManifest
    manifest = CorpusManifest(corpus_id="c", semver="1.0.0", kind="public", tasks=[])
    manifest.calibration.status = "full-run-validated"
    findings2 = compute_findings(ledger, spec, spec.seed, corpus_manifest=manifest,
                                 coverage_n_sim=20, coverage_n_boot=60, n_boot=200)
    official = render_markdown(findings2, ledger, "official", corpus_manifest=manifest)
    assert "UNBLINDED DIAGNOSTIC" not in official
    assert "planning_quality" not in official


# --- AC-7: telemetry juxtaposed + correlation reported ----------------------
def test_ac7_telemetry_juxtaposed():
    tel = {"tool_calls": 12, "wall_time": 30, "tokens": 400, "retries": 1, "timeouts": 0}
    pkt = build_process_packet("transcript", _rubric(), telemetry=tel)
    human = pkt.render_human()
    # each scored dimension is juxtaposed with its declared telemetry correlates
    assert "Deterministic telemetry (juxtaposed)" in human
    assert "tool_calls" in human and "wall_time" in human


def test_ac7_correlation_reported():
    r = _rubric()
    # planning_quality declares [tool_calls, wall_time]; make scores track tool_calls
    rows = {
        "planning_quality": [(1, {"tool_calls": 1, "wall_time": 5}),
                             (2, {"tool_calls": 2, "wall_time": 5}),
                             (3, {"tool_calls": 3, "wall_time": 5}),
                             (4, {"tool_calls": 4, "wall_time": 5})],
        # error_recovery scores are flat vs its correlates ⇒ style-only
        "error_recovery": [(3, {"retries": 1, "timeouts": 9}),
                           (3, {"retries": 2, "timeouts": 1}),
                           (3, {"retries": 3, "timeouts": 5})],
    }
    corr = score_telemetry_correlation(rows, r)
    assert corr["planning_quality"].correlations["tool_calls"] == pytest.approx(1.0)
    assert corr["planning_quality"].style_only is False

    # a dimension whose scores VARY but are uncorrelated with its stated
    # correlates is measuring style, not process ⇒ flagged style_only [AC-7]
    rows_style = {"planning_quality": [(1, {"tool_calls": 3, "wall_time": 2}),
                                       (2, {"tool_calls": 1, "wall_time": 9}),
                                       (3, {"tool_calls": 4, "wall_time": 1}),
                                       (4, {"tool_calls": 2, "wall_time": 5})]}
    style = score_telemetry_correlation(rows_style, r, threshold=0.5)
    assert style["planning_quality"].style_only is True

    # constant telemetry ⇒ correlation undefined ⇒ no measured signal (not flagged)
    rows2 = {"error_recovery": [(1, {"retries": 5, "timeouts": 5}),
                                (5, {"retries": 5, "timeouts": 5}),
                                (3, {"retries": 5, "timeouts": 5})]}
    corr2 = score_telemetry_correlation(rows2, r)
    assert corr2["error_recovery"].correlations["retries"] is None
    assert corr2["error_recovery"].style_only is False


def test_process_score_registered():
    from harness.ledger import events
    assert "process_score" in events.REGISTERED_EVENTS
