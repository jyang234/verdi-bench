"""EVAL-7 RV-4 / RV-5 — escalation calibration through the IPW seam.

The escalation gate must use the D003 IPW estimator over the reviewed set, not
raw pooled Cohen's kappa (which over-penalizes the judge because the reviewed set
is enriched for disagreements), and it must reweight the floor by the *realized*
inclusion probability ceil(0.2n)/n, not the nominal 0.2.
"""

from __future__ import annotations

from harness.judge.schema import Evidence, Verdict, VerdictProvenance, Winner
from harness.ledger.events import append_human_verdict, append_verdict
from harness.review.calibrate import kappa_by_class_ipw
from harness.review.kappa import KappaEstimator, ReviewedItem, estimate_kappa, kappa_report
from harness.review.sample import (
    ComparisonRecord,
    realized_floor_prob,
    reviewed_kappa_items,
    select_for_review,
)
from tests.fixtures.builders import fixed_ctx, seed_trial_and_grade


def _prov(model="google/gemini-1.5-pro-002"):
    return VerdictProvenance(
        judge_model=model, rubric_sha256="a", packet_sha256="b",
        call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t",
    )


def _verdict(winner, cid, *, source="judge", task_class="cls", task_id=None):
    ev = [Evidence(kind="diff", response=winner, hunk="h")] if winner in ("A", "B") else []
    return Verdict(
        winner=Winner(winner), reason="r", evidence=ev,
        provenance=_prov("human" if source == "human" else "google/gemini-1.5-pro-002"),
        source=source, comparison_id=cid, task_class=task_class, task_id=task_id,
    )


def _seed_comparison(ledger, ctx, task_id, *, control_pass, treatment_pass,
                     judge_winner, human_winner, task_class="cls"):
    cid = f"cmp-{task_id}-r0"
    seed_trial_and_grade(ledger, ctx, trial_id=f"c-{task_id}", task_id=task_id,
                         arm="control", passed=control_pass)
    seed_trial_and_grade(ledger, ctx, trial_id=f"t-{task_id}", task_id=task_id,
                         arm="treatment", passed=treatment_pass)
    append_verdict(ledger, ctx, verdict=_verdict(
        judge_winner, cid, task_class=task_class, task_id=task_id).model_dump(mode="json"))
    append_human_verdict(ledger, ctx, verdict=_verdict(
        human_winner, cid, source="human", task_class=task_class, task_id=task_id
    ).model_dump(mode="json"), arm_recognized=False)


# --- RV-5: realized floor probability ---------------------------------------
def test_rv5_realized_floor_prob_exceeds_nominal():
    # 6 agreements -> ceil(0.2*6)=2 floor -> realized prob 2/6, NOT the nominal 0.2
    records = [ComparisonRecord(f"c{i}", "cls", "A", False, "A") for i in range(6)]
    assert abs(realized_floor_prob(records) - 2 / 6) < 1e-12
    # 5 agreements -> ceil(1.0)=1 -> 1/5 = 0.2 (matches nominal at this n)
    records5 = [ComparisonRecord(f"c{i}", "cls", "A", False, "A") for i in range(5)]
    assert abs(realized_floor_prob(records5) - 0.2) < 1e-12


def test_rv5_kappa_report_exposes_floor_prob():
    items = [ReviewedItem("A", "B", "mandatory"), ReviewedItem("A", "A", "floor")]
    rep = kappa_report(items, floor_prob=1 / 3)
    assert abs(rep.floor_prob - 1 / 3) < 1e-12
    assert rep.as_dict()["floor_prob"] == rep.floor_prob


# --- RV-4: escalation through the IPW seam ----------------------------------
def test_rv4_escalation_uses_ipw_not_raw_pooled(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    # two disagreements of opposite direction (judge label varies, so kappa is not
    # trivially degenerate): judge A vs holdouts B, and judge B vs holdouts A ...
    _seed_comparison(ledger, ctx, "d0", control_pass=False, treatment_pass=True,
                     judge_winner="A", human_winner="B")
    _seed_comparison(ledger, ctx, "d1", control_pass=True, treatment_pass=False,
                     judge_winner="B", human_winner="A")
    # ... and six agreements (judge A == holdouts A, human A) -> ceil(0.2*6)=2 floor
    for i in range(6):
        _seed_comparison(ledger, ctx, f"a{i}", control_pass=True, treatment_pass=False,
                         judge_winner="A", human_winner="A")

    cal = kappa_by_class_ipw(ledger, arm_a="control", arm_b="treatment", seed=7,
                             min_human_verdicts=1)
    assert "cls" in cal and cal["cls"].sufficient

    # recompute the reviewed items the gate saw, and confirm the reported kappa is
    # the IPW estimate (floor upweighted) — and that it differs from raw pooled.
    from harness.review.sample import comparisons_from_ledger
    records = comparisons_from_ledger(ledger, arm_a="control", arm_b="treatment")
    selected = select_for_review(records, 7)
    items = reviewed_kappa_items(ledger, selected)
    fp = realized_floor_prob(records)
    ipw = estimate_kappa(items, KappaEstimator.ipw, floor_prob=fp)
    raw = estimate_kappa(items, KappaEstimator.raw_pooled)
    assert abs(cal["cls"].kappa - ipw) < 1e-9
    assert abs(ipw - raw) > 1e-6  # the bias correction actually changed the number


def test_dp7_4_kappa_report_produces_floor_only_sensitivity():
    """D-P7-4: kappa_report yields a defined floor-only sensitivity when the floor
    items carry varied labels (the sensitivity analysis D003 specifies)."""
    items = [
        ReviewedItem("A", "B", "mandatory"),
        ReviewedItem("A", "A", "floor"),
        ReviewedItem("B", "B", "floor"),
    ]
    rep = kappa_report(items, floor_prob=0.5)
    assert rep.headline is not None
    assert rep.sensitivity is not None  # floor-only over [A/A, B/B] is defined


def test_dp7_4_calibration_wires_sensitivity_from_kappa_report(tmp_path):
    """D-P7-4: kappa_by_class_ipw carries exactly the floor-only sensitivity
    kappa_report computes over the items the gate saw — proving the render's
    sensitivity comes through the kappa_report seam, not a re-derivation."""
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    _seed_comparison(ledger, ctx, "d0", control_pass=False, treatment_pass=True,
                     judge_winner="A", human_winner="B")
    _seed_comparison(ledger, ctx, "d1", control_pass=True, treatment_pass=False,
                     judge_winner="B", human_winner="A")
    # mixed agreements (A/A and B/B) so a drawn floor can be non-degenerate
    for i in range(4):
        _seed_comparison(ledger, ctx, f"aa{i}", control_pass=True, treatment_pass=False,
                         judge_winner="A", human_winner="A")
    for i in range(4):
        _seed_comparison(ledger, ctx, f"bb{i}", control_pass=False, treatment_pass=True,
                         judge_winner="B", human_winner="B")

    from harness.review.sample import comparisons_from_ledger

    seed = 3
    cal = kappa_by_class_ipw(ledger, arm_a="control", arm_b="treatment", seed=seed,
                             min_human_verdicts=1)
    records = comparisons_from_ledger(ledger, arm_a="control", arm_b="treatment")
    selected = select_for_review(records, seed)
    items = reviewed_kappa_items(ledger, selected)
    fp = realized_floor_prob(records)
    expected = kappa_report(items, floor_prob=fp)
    assert cal["cls"].sensitivity == expected.sensitivity
    assert cal["cls"].kappa == expected.headline


def test_dp7_4_render_shows_ipw_and_floor_sensitivity():
    """D-P7-4: the exploratory judge-calibration render shows the floor-only
    sensitivity beside the IPW headline kappa."""
    from harness.analyze.report import _judge_calibration_lines

    class _F:
        judge_calibration = {
            "kappa_threshold": 0.6, "min_human_verdicts": 1, "single_order_verdicts": 0,
            "by_class": {"cls": {"kappa": 0.4, "n": 8, "sufficient": True,
                                 "escalate": True, "sensitivity": 0.2}},
            "escalation_candidates": ["cls"],
        }

    text = "\n".join(_judge_calibration_lines(_F()))
    assert "kappa=0.400" in text
    assert "sensitivity (floor-only): kappa=0.200" in text
