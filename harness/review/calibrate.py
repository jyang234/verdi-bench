"""Review calibration through the IPW seam [EVAL-7 §M5, RV-4/RV-5].

Per-class judge-vs-human kappa for the escalation gate, computed through the D003
IPW estimator over the reviewed set — **not** raw pooled Cohen's kappa over the
disagreement-heavy sample (``judge.calibrate.kappa_by_class``), which
systematically under-rates the judge because the reviewed set is enriched for
disagreements. Floor items are reweighted by the **realized** inclusion
probability ``ceil(0.2n)/n`` (RV-5), not the nominal 0.2.
"""

from __future__ import annotations

from collections import defaultdict

from ..judge.calibrate import ClassCalibration
from .kappa import bootstrap_kappa_interval, kappa_report, kish_effective_n
from .sample import (
    comparisons_from_ledger,
    realized_floor_prob,
    reviewed_kappa_items,
    select_for_review,
)


def calibration_from_spec(ledger_path, spec, seed: int) -> dict[str, ClassCalibration]:
    """Per-class IPW kappa at the spec's locked ``EscalationConfig`` — the single
    seam both ``bench judge`` and the analyze render call, so the escalation
    wiring (which arms, seed, thresholds feed calibration) cannot drift between
    them [JD-9, RV-4]."""
    esc = spec.judge.escalation
    return kappa_by_class_ipw(
        ledger_path, arm_a=spec.arms[0].name, arm_b=spec.arms[1].name, seed=seed,
        kappa_threshold=esc.kappa_threshold, min_human_verdicts=esc.min_human_verdicts,
    )


def kappa_by_class_ipw(
    ledger_path,
    *,
    arm_a: str,
    arm_b: str,
    seed: int,
    kappa_threshold: float = 0.6,
    min_human_verdicts: int = 20,
) -> dict[str, ClassCalibration]:
    """Per-class IPW kappa + escalation flags over the reviewed set [RV-4].

    Classes with fewer than ``min_human_verdicts`` reviewed items are
    ``insufficient``; sufficient classes below ``kappa_threshold`` are escalation
    candidates (v1 = flag only).
    """
    records = comparisons_from_ledger(ledger_path, arm_a=arm_a, arm_b=arm_b)
    selected = select_for_review(records, seed)
    floor_prob = realized_floor_prob(records)
    items = reviewed_kappa_items(ledger_path, selected)

    by_class: dict[str, list] = defaultdict(list)
    for it in items:
        by_class[it.task_class or "default"].append(it)

    out: dict[str, ClassCalibration] = {}
    for cls, cls_items in by_class.items():
        n = len(cls_items)
        # F-M-S4: sufficiency is gated on the Kish EFFECTIVE sample size — a
        # raw count floor let a handful of 5×-reweighted floor items masquerade
        # as twenty items' worth of information.
        n_eff = kish_effective_n(cls_items, floor_prob)
        if n_eff < min_human_verdicts:
            out[cls] = ClassCalibration(
                cls, n, kappa=None, sufficient=False, escalate=False, n_eff=n_eff
            )
            continue
        # D-P7-4: compute the IPW headline AND the floor-only sensitivity through
        # kappa_report (its production caller), so the render can show both.
        report = kappa_report(cls_items, floor_prob=floor_prob)
        k = report.headline
        if k is None:
            # D-5: degenerate marginals ⇒ no chance-corrected information;
            # insufficient, not perfect, and cannot escalate on undefined.
            out[cls] = ClassCalibration(
                cls, n, kappa=None, sufficient=False, escalate=False, n_eff=n_eff
            )
            continue
        # F-M-S4: escalation gates on the INTERVAL, not the point estimate —
        # escalate only when the judge is confidently below threshold (upper
        # bound < threshold); an interval that straddles it (or no usable
        # interval) is INCONCLUSIVE: enough data to gate, not enough precision
        # to decide, rendered as such rather than silently not-escalated.
        ci = bootstrap_kappa_interval(cls_items, floor_prob=floor_prob, seed=seed)
        escalate = ci is not None and ci[1] < kappa_threshold
        inconclusive = ci is None or (ci[0] < kappa_threshold <= ci[1])
        out[cls] = ClassCalibration(
            cls, n, kappa=k, sufficient=True, escalate=escalate,
            sensitivity=report.sensitivity,
            kappa_ci=ci, n_eff=n_eff, inconclusive=inconclusive,
        )
    return out
