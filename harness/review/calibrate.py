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
from .kappa import KappaEstimator, estimate_kappa
from .sample import (
    comparisons_from_ledger,
    realized_floor_prob,
    reviewed_kappa_items,
    select_for_review,
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
        if n < min_human_verdicts:
            out[cls] = ClassCalibration(cls, n, kappa=None, sufficient=False, escalate=False)
            continue
        k = estimate_kappa(cls_items, KappaEstimator.ipw, floor_prob=floor_prob)
        out[cls] = ClassCalibration(
            cls, n, kappa=k, sufficient=True, escalate=k < kappa_threshold
        )
    return out
