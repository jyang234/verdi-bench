"""Calibration subset selection [EVAL-8 §M2, AC-2, D001].

``calibration_subset`` draws a seed-derived, stratified ~30-task subset for
plumbing validation. Strata come from dataset metadata (``category``/
``difficulty`` as available); allocation is proportional with a deterministic
largest-remainder tie-break; selection within a stratum is a seeded shuffle. The
selection is a pure function of ``(manifest, seed, target_size, stratum_key)`` —
no numpy, no wall clock — and the strata definition is recorded in the manifest
so the choice stays auditable even when metadata is thin [risks §9].
"""

from __future__ import annotations

from ..plan.seeds import index_at, sub_seed
from .registry import CalibrationSubset, CorpusManifest

DEFAULT_TARGET = 30


def _seeded_shuffle(items: list[str], base: int) -> list[str]:
    """Fisher–Yates under full-width per-step hashing — same primitive as the
    interleave, so no LCG bias and reproducible for ``base``."""
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = index_at(base, i, i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def _proportional_allocation(sizes: dict[str, int], target: int) -> dict[str, int]:
    """Largest-remainder proportional allocation of ``target`` across strata.

    Deterministic: remainders tie-break by stratum name so the result depends
    only on ``(sizes, target)``.
    """
    total = sum(sizes.values())
    if total == 0:
        return {k: 0 for k in sizes}
    target = min(target, total)
    exact = {k: target * n / total for k, n in sizes.items()}
    floors = {k: int(v) for k, v in exact.items()}
    remaining = target - sum(floors.values())
    # distribute the remainder to the largest fractional parts, name as tie-break
    order = sorted(sizes, key=lambda k: (-(exact[k] - floors[k]), k))
    for k in order[:remaining]:
        floors[k] += 1
    # never allocate more than a stratum holds
    for k in floors:
        floors[k] = min(floors[k], sizes[k])
    return floors


def calibration_subset(
    manifest: CorpusManifest,
    seed: int,
    *,
    target_size: int = DEFAULT_TARGET,
    stratum_key: str = "category",
) -> CalibrationSubset:
    """Select and record a stratified calibration subset on ``manifest``.

    Mutates ``manifest.calibration.subset`` and returns it. Tasks lacking the
    stratum key fall into an explicit ``"unstratified"`` bucket rather than
    being silently dropped.
    """
    buckets: dict[str, list[str]] = {}
    for t in sorted(manifest.tasks, key=lambda t: t.task_id):
        stratum = str(t.metadata.get(stratum_key, "unstratified"))
        buckets.setdefault(stratum, []).append(t.task_id)

    sizes = {k: len(v) for k, v in buckets.items()}
    allocation = _proportional_allocation(sizes, target_size)

    base = sub_seed(seed, "calibration_subset")
    chosen: list[str] = []
    for stratum in sorted(buckets):
        n = allocation[stratum]
        if n <= 0:
            continue
        # namespace the shuffle per stratum so strata don't share a draw
        stratum_base = sub_seed(base, stratum)
        picked = _seeded_shuffle(buckets[stratum], stratum_base)[:n]
        chosen.extend(sorted(picked))

    subset = CalibrationSubset(
        seed=seed,
        strata={"stratum_key": stratum_key, "allocation": allocation, "sizes": sizes},
        task_ids=sorted(chosen),
    )
    # Selection records the subset; it does NOT validate it. Status advances only
    # when a calibration run is recorded (see CorpusManifest.record_calibration_run).
    manifest.calibration.subset = subset
    return subset
