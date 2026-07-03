"""Cost guard [EVAL-4 §M5, AC-7, EVAL-1-D007].

Accumulates ``cost`` across trial records and answers whether a further trial may
start (:meth:`would_exceed`). A null cost contributes nothing (it is unmeasurable,
never estimated) — the guard is conservative by design. The scheduler
(:mod:`harness.run.interleave`) owns the stop decision and the
``run_stopped_cost_ceiling`` ledger event; this guard only tracks the running
total and the ceiling check.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostGuard:
    ceiling: float
    accumulated: float = 0.0

    def add(self, cost: float | None) -> None:
        if cost is not None:
            self.accumulated += cost

    def would_exceed(self) -> bool:
        """True if no further trial may start (already at/over the ceiling)."""
        return self.accumulated >= self.ceiling

    def remaining(self) -> float:
        return max(0.0, self.ceiling - self.accumulated)
