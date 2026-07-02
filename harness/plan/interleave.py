"""Deterministic interleave derivation [EVAL-3 AC-5, D005].

``derive_schedule`` is the pure function EVAL-4 executes. Same locked plan ⇒
identical schedule; a different seed ⇒ a different recorded order. The sub-seed
is namespaced (``sha256(seed || "interleave")``) so this stream can't perturb
other seeded stages [master plan §7.5].
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Trial:
    """A single unit of execution: one (task, arm, repetition)."""

    task_id: str
    arm: str
    repetition: int

    def key(self) -> str:
        return f"{self.task_id}|{self.arm}|{self.repetition}"


def _sub_seed(seed: int, purpose: str) -> int:
    h = hashlib.sha256(f"{seed}||{purpose}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def enumerate_trials(tasks: list[str], arms: list[str], repetitions: int) -> list[Trial]:
    """The full trial set in a fixed canonical order (pre-shuffle)."""
    trials: list[Trial] = []
    for task in tasks:
        for arm in arms:
            for rep in range(repetitions):
                trials.append(Trial(task_id=task, arm=arm, repetition=rep))
    return trials


def derive_schedule(seed: int, trials: list[Trial]) -> list[Trial]:
    """Fisher–Yates shuffle of ``trials`` under a namespaced sub-seed.

    Pure and reproducible: no global RNG, no wall clock. The shuffle uses a
    deterministic LCG stream seeded from ``sha256(seed || "interleave")`` so the
    result depends only on ``(seed, trials)``.
    """
    out = list(trials)
    state = _sub_seed(seed, "interleave")
    # A full-period LCG (Numerical Recipes constants) drives Fisher–Yates.
    for i in range(len(out) - 1, 0, -1):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFFFFFFFFFF
        j = state % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out
