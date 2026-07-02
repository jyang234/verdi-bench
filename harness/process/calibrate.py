"""Process calibration + telemetry correlation [EVAL-9 §M4, §M5, AC-5, AC-7].

Per-dimension judge↔human agreement is **quadratic-weighted kappa** (ordinal
1..5) with the same IPW correction and gate mechanics as outcome kappa — the
estimator is imported from EVAL-7's seam, never reimplemented [AC-5]. Dimension
gates escalate **independently**: a dimension below threshold is flagged without
dragging the others.

The score-vs-telemetry correlation (Spearman per dimension against its declared
correlates) is the AC-7 sanity check: a dimension uncorrelated with its own
stated correlates is measuring style, not process — flag it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ..review.kappa import KappaEstimator, ReviewedItem, estimate_kappa
from .rubric import SCALE_MAX, SCALE_MIN, ProcessRubric

_ORDINAL_CATEGORIES = list(range(SCALE_MIN, SCALE_MAX + 1))
DEFAULT_KAPPA_THRESHOLD = 0.6
DEFAULT_CORRELATION_THRESHOLD = 0.2


@dataclass(frozen=True)
class DimensionCalibration:
    dim_id: str
    n: int
    kappa: Optional[float]
    sufficient: bool
    escalate: bool


def process_kappa_by_dimension(
    items_by_dim: dict[str, Sequence[ReviewedItem]],
    *,
    kappa_threshold: float = DEFAULT_KAPPA_THRESHOLD,
    min_pairs: int = 1,
    estimator: KappaEstimator | str = KappaEstimator.ipw,
) -> dict[str, DimensionCalibration]:
    """Quadratic-weighted, IPW-corrected kappa per dimension; gates independently."""
    out: dict[str, DimensionCalibration] = {}
    for dim_id, items in items_by_dim.items():
        items = list(items)
        n = len(items)
        if n < min_pairs:
            out[dim_id] = DimensionCalibration(dim_id, n, None, sufficient=False, escalate=False)
            continue
        k = estimate_kappa(
            items, estimator, weight="quadratic", categories=_ORDINAL_CATEGORIES
        )
        out[dim_id] = DimensionCalibration(
            dim_id, n, kappa=k, sufficient=True, escalate=k < kappa_threshold
        )
    return out


# --- Spearman correlation (scipy-free) -------------------------------------
def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # average 1-based rank for ties
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def _spearman(x: list[float], y: list[float]) -> Optional[float]:
    if len(x) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None  # no variance ⇒ correlation undefined
    return cov / (vx ** 0.5 * vy ** 0.5)


@dataclass(frozen=True)
class DimensionCorrelation:
    dim_id: str
    correlations: dict[str, Optional[float]]
    style_only: bool  # uncorrelated with all its declared correlates


def score_telemetry_correlation(
    rows_by_dim: dict[str, list[tuple]],
    rubric: ProcessRubric,
    *,
    threshold: float = DEFAULT_CORRELATION_THRESHOLD,
) -> dict[str, DimensionCorrelation]:
    """Spearman of each dimension's scores vs its declared telemetry correlates.

    ``rows_by_dim`` = ``{dim_id: [(score, {correlate: value})]}`` (CANT_SCORE rows
    already excluded). A dimension whose |rho| stays below ``threshold`` for every
    declared correlate is flagged ``style_only`` [AC-7].
    """
    out: dict[str, DimensionCorrelation] = {}
    for d in rubric.dimensions:
        rows = rows_by_dim.get(d.id, [])
        scores = [float(s) for s, _ in rows]
        correlations: dict[str, Optional[float]] = {}
        for correlate in d.telemetry_correlates:
            vals = [row_tel.get(correlate) for _, row_tel in rows]
            if any(v is None for v in vals) or len(vals) < 2:
                correlations[correlate] = None
                continue
            correlations[correlate] = _spearman(scores, [float(v) for v in vals])
        measured = [abs(r) for r in correlations.values() if r is not None]
        style_only = bool(measured) and max(measured) < threshold
        out[d.id] = DimensionCorrelation(d.id, correlations, style_only)
    return out
