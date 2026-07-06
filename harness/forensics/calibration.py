"""Per-detector kappa calibration — the LLM↔human spot-check join [EVAL-11 AC-4].

Split out of ``review.py`` [refactor 06 §5]: the blinded advisory LLM review and
this calibration join are two concerns. Calibration reuses EVAL-9's kappa
machinery verbatim [AC-4] — per-detector judge↔human agreement over binary flags
is *unweighted* IPW-corrected kappa (the detector vocabulary is nominal, not
ordinal), pairing the advisory pass's suspicions (from the latest
``forensics_report``) with ledgered human spot-checks (``forensic_spotcheck``
events) into the table analyze folds into findings [D006].

Deterministic and provider-free by construction: it reads ledgered suspicions and
human labels and runs the shared kappa gate — no LLM client. ``review.py``
re-exports these names, so ``harness.forensics.review.spotcheck_kappa`` (the path
analyze and the AC-4 tests reach) keeps resolving.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ..review.kappa import (
    FLOOR_INCLUSION_PROB,
    KappaEstimator,
    ReviewedItem,
    keyed_kappa_gate,
)

_BINARY_CATEGORIES = [0, 1]
DEFAULT_KAPPA_THRESHOLD = 0.6


@dataclass(frozen=True)
class DetectorCalibration:
    detector_id: str
    n: int
    kappa: Optional[float]
    sufficient: bool
    escalate: bool


def detector_kappa(
    items_by_detector: dict[str, Sequence[ReviewedItem]],
    *,
    kappa_threshold: float = DEFAULT_KAPPA_THRESHOLD,
    min_pairs: int = 1,
    estimator: KappaEstimator | str = KappaEstimator.ipw,
    floor_prob: float = FLOOR_INCLUSION_PROB,
) -> dict[str, DetectorCalibration]:
    """Unweighted, IPW-corrected kappa per detector; gates independently — the
    shared :func:`keyed_kappa_gate` mechanics over binary flag categories, so
    the gate cannot drift from EVAL-9's per-dimension tier."""
    gated = keyed_kappa_gate(
        items_by_detector,
        weight="unweighted",
        categories=_BINARY_CATEGORIES,
        kappa_threshold=kappa_threshold,
        min_pairs=min_pairs,
        estimator=estimator,
        floor_prob=floor_prob,
    )
    return {
        detector_id: DetectorCalibration(detector_id, c.n, c.kappa, c.sufficient, c.escalate)
        for detector_id, c in gated.items()
    }


def spotcheck_kappa(ledger_path, *, spec=None, report: Optional[dict] = None) -> dict:
    """Pair the latest forensics_report's LLM suspicions with ledgered human
    spot-checks (``forensic_spotcheck`` events) into the per-detector kappa
    table analyze folds into findings [AC-4, D006].

    Strata ride the spot-check events themselves (recorded against the EVAL-7
    reviewed sample). When ``spec`` is provided the IPW correction uses the
    sample's *realized* floor inclusion probability (``ceil(0.2n)/n``, the
    RV-5 correction outcome and process kappa both use), not the nominal 0.2.
    ``report`` short-circuits the latest-event fetch when the caller already
    holds the forensics_report payload.
    """
    from collections import defaultdict

    from ..ledger import events
    from ..ledger.query import find_events, latest_event

    if report is None:
        report_ev = latest_event(ledger_path, events.FORENSICS_REPORT)
        report = (report_ev or {}).get("forensics_report", {})
    reviews = report.get("reviews") or {}
    items: dict[str, list[ReviewedItem]] = defaultdict(list)
    n_spotchecks = 0
    for ev in find_events(ledger_path, events.FORENSIC_SPOTCHECK):
        sc = ev["forensic_spotcheck"]
        n_spotchecks += 1
        review = reviews.get(sc["trial_id"])
        if not review or review.get("suspicions") is None:
            continue  # unreviewed or CANT_REVIEW trials cannot calibrate
        for detector_id, human_label in sc["labels"].items():
            llm_label = review["suspicions"].get(detector_id)
            if llm_label is None:
                continue
            items[detector_id].append(
                ReviewedItem(
                    a=int(llm_label), b=int(bool(human_label)), stratum=sc["stratum"]
                )
            )
    floor_prob = FLOOR_INCLUSION_PROB
    if spec is not None and items:
        from ..review.sample import comparisons_from_ledger, realized_floor_prob

        records = comparisons_from_ledger(
            ledger_path, arm_a=spec.arms[0].name, arm_b=spec.arms[1].name
        )
        if records:
            floor_prob = realized_floor_prob(records)
    calibrations = detector_kappa(items, floor_prob=floor_prob)
    return {
        "n_spotchecks": n_spotchecks,
        "floor_prob": floor_prob,
        "kappa_by_detector": {
            d: {"kappa": c.kappa, "n": c.n, "sufficient": c.sufficient,
                "escalate": c.escalate}
            for d, c in sorted(calibrations.items())
        },
    }
