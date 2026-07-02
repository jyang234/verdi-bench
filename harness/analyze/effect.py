"""Effect sizes [EVAL-6 §M2, D001, AC-2].

Two mandatory effect measures per comparison:

* **mean paired delta** — the average per-task (A − B) difference.
* **Cliff's delta** — a nonparametric dominance measure over the two arms'
  per-task value distributions, in [-1, 1], computed in O(n log n) via sorted
  binary search and fixture-verified against hand-checked values.

Both are mandatory: a report fixture missing either fails render validation
(:mod:`.report`).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass


def cliffs_delta(a_values, b_values) -> float:
    """Cliff's delta = P(A>B) − P(A<B) over all n_a·n_b arm-value pairs.

    O(n log n): sort B once, then for each A value count B-values strictly below
    and strictly above via binary search.
    """
    a = list(a_values)
    b = sorted(b_values)
    n_a, n_b = len(a), len(b)
    if n_a == 0 or n_b == 0:
        raise ValueError("cliffs_delta needs non-empty value vectors for both arms")
    dominance = 0
    for x in a:
        less = bisect_left(b, x)        # B strictly below x
        greater = n_b - bisect_right(b, x)  # B strictly above x
        dominance += less - greater
    return dominance / (n_a * n_b)


@dataclass(frozen=True)
class EffectSizes:
    mean_paired_delta: float
    cliffs_delta: float

    def as_dict(self) -> dict:
        return {
            "mean_paired_delta": self.mean_paired_delta,
            "cliffs_delta": self.cliffs_delta,
        }


def effect_sizes(a_values, b_values) -> EffectSizes:
    """Compute both mandatory effect sizes for the paired arms A and B.

    ``a_values``/``b_values`` are the per-task values for arm A and arm B (already
    reduced over repetitions), paired by task index for the mean paired delta.
    """
    a = list(a_values)
    b = list(b_values)
    if len(a) != len(b):
        raise ValueError("paired arms must have equal per-task value counts")
    if not a:
        raise ValueError("effect_sizes needs at least one paired task")
    mean_paired = sum(x - y for x, y in zip(a, b)) / len(a)
    return EffectSizes(mean_paired_delta=mean_paired, cliffs_delta=cliffs_delta(a, b))
