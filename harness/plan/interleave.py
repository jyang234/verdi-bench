"""Deterministic interleave derivation [EVAL-3 AC-5, D005].

``derive_schedule`` is the pure function EVAL-4 executes. Same locked plan ⇒
identical schedule; a different seed ⇒ a different recorded order. The sub-seed
is namespaced (``sha256(seed || "interleave")``) so this stream can't perturb
other seeded stages [master plan §7.5].
"""

from __future__ import annotations

from dataclasses import dataclass

from .seeds import index_at, sub_seed


@dataclass(frozen=True)
class Trial:
    """A single unit of execution: one (task, arm, repetition)."""

    task_id: str
    arm: str
    repetition: int

    def key(self) -> str:
        return f"{self.task_id}|{self.arm}|{self.repetition}"


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

    Pure and reproducible: no global RNG, no wall clock. Each swap index is drawn
    from the full-width bits of a per-step SHA-256 (``index_at``) rather than the
    low bits of an LCG — the latter have very short periods and would bias a
    schedule whose whole job is to decorrelate arm/order effects. The result
    depends only on ``(seed, trials)``.
    """
    out = list(trials)
    base = sub_seed(seed, "interleave")
    for i in range(len(out) - 1, 0, -1):
        j = index_at(base, i, i + 1)
        out[i], out[j] = out[j], out[i]
    return out
