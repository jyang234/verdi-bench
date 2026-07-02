"""Cost guard [EVAL-4 §M5, AC-7, EVAL-1-D007].

Accumulates ``cost`` across trial records; before each trial start, refuses to
begin if the accumulated cost is at/over the ceiling and appends a
``run_stopped_cost_ceiling`` event. A null cost contributes nothing (it is
unmeasurable, never estimated) — the guard is conservative by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostGuard:
    ceiling: float
    accumulated: float = 0.0
    stopped: bool = field(default=False, init=False)

    def add(self, cost: float | None) -> None:
        if cost is not None:
            self.accumulated += cost

    def would_exceed(self) -> bool:
        """True if no further trial may start (already at/over the ceiling)."""
        return self.accumulated >= self.ceiling

    def remaining(self) -> float:
        return max(0.0, self.ceiling - self.accumulated)
