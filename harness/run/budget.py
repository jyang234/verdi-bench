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


def enforcement_cost(
    telemetry_cost: float | None, proxy_metered_cost: float | None
) -> float | None:
    """Cost figure the guard enforces on [RN-2, F-H4].

    The proxy is the out-of-band meter; the telemetry figure is the arm's own
    claim. When both exist, enforcement takes the LARGER — under-reporting must
    not buy budget. When only one exists, it is used as-is; when neither does,
    the spend is unmeasurable and contributes nothing (conservative by design).

    Enforcement only: this never fills ``telemetry.cost`` in the record (D004
    keeps nulls null, and the recorded self-report is never rewritten).
    """
    if telemetry_cost is not None and proxy_metered_cost is not None:
        return max(telemetry_cost, proxy_metered_cost)
    return telemetry_cost if telemetry_cost is not None else proxy_metered_cost


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
