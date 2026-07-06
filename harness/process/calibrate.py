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

from ..review.kappa import (
    FLOOR_INCLUSION_PROB,
    KappaEstimator,
    ReviewedItem,
    keyed_kappa_gate,
)
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
    # F-M-S4: aligned with the outcome tier's escalation floor — one reviewed
    # pair must never render a dimension as 'sufficient'.
    min_pairs: int = 20,
    estimator: KappaEstimator | str = KappaEstimator.ipw,
    floor_prob: float = FLOOR_INCLUSION_PROB,
) -> dict[str, DimensionCalibration]:
    """Quadratic-weighted, IPW-corrected kappa per dimension; gates independently.

    ``floor_prob`` is the realized floor inclusion probability ``ceil(0.2n)/n`` —
    the same correction outcome kappa uses [RV-5], passed by the caller. The
    gate mechanics (min-pairs, degenerate-marginals-are-insufficient [D-5],
    threshold escalation) live once in :func:`keyed_kappa_gate`; only the
    ordinal weighting is this tier's.
    """
    gated = keyed_kappa_gate(
        items_by_dim,
        weight="quadratic",
        categories=_ORDINAL_CATEGORIES,
        kappa_threshold=kappa_threshold,
        min_pairs=min_pairs,
        estimator=estimator,
        floor_prob=floor_prob,
    )
    return {
        dim_id: DimensionCalibration(dim_id, c.n, c.kappa, c.sufficient, c.escalate)
        for dim_id, c in gated.items()
    }


def dimension_diagnostics(
    ledger_path,
    spec,
    seed: int,
    *,
    rubric: Optional[ProcessRubric] = None,
    dim_ids: Optional[Sequence[str]] = None,
    exclude_trials: frozenset[str] | set[str] = frozenset(),
):
    """Assemble per-dimension judge↔human kappa + score-vs-telemetry correlations
    from the ledger [PR-5, AC-5/AC-7].

    Judge and human ``process_score`` events are paired by trial; kappa runs over
    the trials in the EVAL-7 reviewed set (so the IPW strata apply), while the
    correlation runs over every judge-scored trial vs its telemetry. Returns the
    diagnostics block ``analyze`` folds into its process section.

    ``dim_ids`` (P4-RUBRIC option (a), [refactor 06 §7]) is the set of dimensions
    to report — the analyze fold passes the UNION of ledgered dim_ids, so the
    kappa/correlation tables cover exactly what the means table above them shows,
    never silently the default v1 rubric's dimensions. Defaults to the rubric's
    own dimensions (used by direct callers/tests on the default rubric).

    ``exclude_trials`` drops those trials' scores from every table here — the
    EVAL-11 D007 quarantine path, applied at the same point as the section's
    per-dimension means so the diagnostics cannot disagree with them.
    """
    from collections import defaultdict

    from ..ledger import events
    from ..ledger.query import find_events
    from ..review.sample import (
        comparisons_from_ledger,
        realized_floor_prob,
        select_for_review,
    )
    from .rubric import default_rubric

    rubric = rubric or default_rubric()
    dims = list(dim_ids) if dim_ids is not None else list(rubric.dimension_ids)

    judge_by_trial: dict[str, dict] = {}
    human_by_trial: dict[str, dict] = {}
    cid_by_trial: dict[str, str] = {}
    for ev in find_events(ledger_path, events.PROCESS_SCORE):
        ps = ev["process_score"]
        if ps["trial_id"] in exclude_trials:
            continue
        kind = ps["provenance"]["scorer"]["kind"]
        smap = {ds["dim_id"]: ds.get("score") for ds in ps["scores"]}
        (judge_by_trial if kind == "judge" else human_by_trial)[ps["trial_id"]] = smap
        if ps.get("comparison_id"):
            cid_by_trial[ps["trial_id"]] = ps["comparison_id"]

    tel_by_trial = {
        ev["trial_record"]["trial_id"]: ev["trial_record"].get("telemetry", {})
        for ev in find_events(ledger_path, events.TRIAL)
    }

    records = comparisons_from_ledger(
        ledger_path, arm_a=spec.arms[0].name, arm_b=spec.arms[1].name
    )
    selected = select_for_review(records, seed)
    strata = {s.comparison_id: (s.stratum, s.task_class) for s in selected}
    floor_prob = realized_floor_prob(records)

    items_by_dim: dict[str, list] = defaultdict(list)
    for trial_id, jmap in judge_by_trial.items():
        hmap = human_by_trial.get(trial_id)
        if hmap is None:
            continue
        st = strata.get(cid_by_trial.get(trial_id))
        if st is None:
            continue
        stratum, task_class = st
        for dim in dims:
            js, hs = jmap.get(dim), hmap.get(dim)
            if js is None or hs is None:  # CANT_SCORE excluded from kappa
                continue
            items_by_dim[dim].append(
                ReviewedItem(a=js, b=hs, stratum=stratum, task_class=task_class)
            )
    kappa = process_kappa_by_dimension(items_by_dim, floor_prob=floor_prob)

    rows_by_dim: dict[str, list] = defaultdict(list)
    for trial_id, jmap in judge_by_trial.items():
        tel = tel_by_trial.get(trial_id, {})
        for dim in dims:
            js = jmap.get(dim)
            if js is not None:
                rows_by_dim[dim].append((js, tel))
    corr = score_telemetry_correlation(rows_by_dim, rubric, dim_ids=dims)

    return {
        "floor_prob": floor_prob,
        "kappa_by_dimension": {
            d: {"kappa": c.kappa, "n": c.n, "sufficient": c.sufficient, "escalate": c.escalate}
            for d, c in sorted(kappa.items())
        },
        "correlations": {
            d: {"correlations": c.correlations, "style_only": c.style_only}
            for d, c in sorted(corr.items())
        },
        "style_only": sorted(d for d, c in corr.items() if c.style_only),
    }


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
    dim_ids: Optional[Sequence[str]] = None,
    threshold: float = DEFAULT_CORRELATION_THRESHOLD,
) -> dict[str, DimensionCorrelation]:
    """Spearman of each dimension's scores vs its declared telemetry correlates.

    ``rows_by_dim`` = ``{dim_id: [(score, {correlate: value})]}`` (CANT_SCORE rows
    already excluded). A dimension whose |rho| stays below ``threshold`` for every
    declared correlate is flagged ``style_only`` [AC-7].

    ``dim_ids`` (P4-RUBRIC option (a), [refactor 06 §7]) is the set of dimensions
    to report — the UNION of ledgered dim_ids when the caller has it, so a
    custom-rubric dimension is not silently dropped for being absent from the
    default rubric. A dimension the rubric does not know has no declared
    correlates (an empty, honest correlation row), never a fabricated one.
    Defaults to the rubric's own dimensions.
    """
    ids = list(dim_ids) if dim_ids is not None else list(rubric.dimension_ids)
    out: dict[str, DimensionCorrelation] = {}
    for dim_id in ids:
        d = rubric.dimension(dim_id)  # None for a ledgered dim the rubric lacks
        correlates = d.telemetry_correlates if d is not None else []
        rows = rows_by_dim.get(dim_id, [])
        scores = [float(s) for s, _ in rows]
        correlations: dict[str, Optional[float]] = {}
        for correlate in correlates:
            vals = [row_tel.get(correlate) for _, row_tel in rows]
            if any(v is None for v in vals) or len(vals) < 2:
                correlations[correlate] = None
                continue
            correlations[correlate] = _spearman(scores, [float(v) for v in vals])
        measured = [abs(r) for r in correlations.values() if r is not None]
        style_only = bool(measured) and max(measured) < threshold
        out[dim_id] = DimensionCorrelation(dim_id, correlations, style_only)
    return out
