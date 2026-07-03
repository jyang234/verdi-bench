"""Kappa estimator seam [EVAL-7 §M5, D003].

The reviewed set is a *biased sample* — every disagreement plus a 20% random
floor of agreements — so a raw pooled kappa over-weights disagreements. The
default estimator is inverse-probability-weighted (IPW): floor items, sampled at
probability 0.2, are reweighted 1/0.2 = 5; mandatory items carry weight 1. A
floor-only kappa is reported as a sensitivity analysis; ``raw_pooled`` is
retained for comparison. D003's resolution flips only the default.

``weighted_kappa`` is general: it takes optional per-item **sample weights**
(the IPW correction) and an optional category **disagreement weighting**
(``unweighted`` nominal, or ``quadratic``/``linear`` for ordinal scales). EVAL-9
imports this same function for per-dimension quadratic-weighted kappa — one
implementation, fixture-verified, not two.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

FLOOR_INCLUSION_PROB = 0.2


def weighted_kappa(
    a: Sequence,
    b: Sequence,
    *,
    sample_weights: Optional[Sequence[float]] = None,
    weight: str = "unweighted",
    categories: Optional[list] = None,
) -> float:
    """Cohen's kappa with optional sample weights and category disagreement weights.

    ``weight``: ``"unweighted"`` (nominal — reduces to Cohen's kappa),
    ``"linear"`` or ``"quadratic"`` (ordinal). ``sample_weights`` reweight items
    (IPW). Returns 1.0 when there is no expected disagreement and observed
    disagreement is also zero; 0.0 when expected disagreement is ~0 but observed
    is not (no reliable signal), mirroring the base ``cohens_kappa`` guard.
    """
    a = list(a)
    b = list(b)
    if len(a) != len(b):
        raise ValueError("label sequences must be equal length")
    if not a:
        raise ValueError("no paired labels")
    w = list(sample_weights) if sample_weights is not None else [1.0] * len(a)
    if len(w) != len(a):
        raise ValueError("sample_weights must match label count")

    cats = categories if categories is not None else sorted(set(a) | set(b))
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    total = float(sum(w))
    if total <= 0:
        raise ValueError("sample weights sum to zero")

    # weighted observed joint distribution
    obs = [[0.0] * k for _ in range(k)]
    for ai, bi, wi in zip(a, b, w):
        obs[idx[ai]][idx[bi]] += wi
    for i in range(k):
        for j in range(k):
            obs[i][j] /= total
    row = [sum(obs[i][j] for j in range(k)) for i in range(k)]
    col = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    def disagreement(i: int, j: int) -> float:
        if weight == "quadratic":
            return ((i - j) / (k - 1)) ** 2 if k > 1 else 0.0
        if weight == "linear":
            return abs(i - j) / (k - 1) if k > 1 else 0.0
        return 0.0 if i == j else 1.0

    num = sum(disagreement(i, j) * obs[i][j] for i in range(k) for j in range(k))
    den = sum(disagreement(i, j) * row[i] * col[j] for i in range(k) for j in range(k))
    if den < 1e-12:
        return 1.0 if num < 1e-12 else 0.0
    return 1.0 - num / den


class KappaEstimator(str, Enum):
    ipw = "ipw"
    floor_only = "floor_only"
    raw_pooled = "raw_pooled"


@dataclass(frozen=True)
class ReviewedItem:
    """One reviewed comparison: paired labels + inclusion stratum."""

    a: object          # judge label / score
    b: object          # human label / score
    stratum: str       # "mandatory" | "floor"
    task_class: Optional[str] = None  # for per-class escalation [RV-4]


def estimate_kappa(
    items: Sequence[ReviewedItem],
    method: KappaEstimator | str = KappaEstimator.ipw,
    *,
    weight: str = "unweighted",
    floor_prob: float = FLOOR_INCLUSION_PROB,
    categories: Optional[list] = None,
) -> float:
    """Estimate kappa over the reviewed set under the chosen correction [D003]."""
    method = KappaEstimator(method)
    used = list(items)
    sample_weights: Optional[list[float]] = None
    if method is KappaEstimator.floor_only:
        used = [i for i in items if i.stratum == "floor"]
    elif method is KappaEstimator.ipw:
        sample_weights = [
            1.0 / floor_prob if i.stratum == "floor" else 1.0 for i in used
        ]
    if not used:
        raise ValueError(f"no reviewed items for estimator {method.value}")
    return weighted_kappa(
        [i.a for i in used],
        [i.b for i in used],
        sample_weights=sample_weights,
        weight=weight,
        categories=categories,
    )


@dataclass(frozen=True)
class KappaReport:
    headline_method: str
    headline: float
    sensitivity_method: str
    sensitivity: Optional[float]
    floor_prob: float = FLOOR_INCLUSION_PROB  # the inclusion prob used for IPW [RV-5]

    def as_dict(self) -> dict:
        return {
            "headline_method": self.headline_method,
            "headline": self.headline,
            "sensitivity_method": self.sensitivity_method,
            "sensitivity": self.sensitivity,
            "floor_prob": self.floor_prob,
        }


def kappa_report(
    items: Sequence[ReviewedItem],
    *,
    weight: str = "unweighted",
    categories=None,
    floor_prob: float = FLOOR_INCLUSION_PROB,
) -> KappaReport:
    """Headline IPW kappa + floor-only sensitivity [D003 rec].

    ``floor_prob`` is the inclusion probability the floor was actually drawn at —
    pass the **realized** ``ceil(0.2n)/n`` (RV-5), not the nominal 0.2. It is
    surfaced on the report so a consumer can see the weighting that produced the
    headline.
    """
    headline = estimate_kappa(
        items, KappaEstimator.ipw, weight=weight, categories=categories, floor_prob=floor_prob
    )
    try:
        sensitivity = estimate_kappa(
            items, KappaEstimator.floor_only, weight=weight, categories=categories
        )
    except ValueError:
        sensitivity = None  # no floor items yet
    return KappaReport(
        headline_method="ipw",
        headline=headline,
        sensitivity_method="floor_only",
        sensitivity=sensitivity,
        floor_prob=floor_prob,
    )
