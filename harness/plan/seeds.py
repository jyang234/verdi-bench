"""Namespaced sub-seeding [master plan §7.5].

A single definition of ``sub_seed`` so the interleave and MDE streams derive from
the same primitive; a change here can't silently desync them. Sub-seeds are
namespaced by purpose (``sha256(seed || purpose)``) so stages can't perturb each
other's streams.
"""

from __future__ import annotations

import hashlib


def sub_seed(seed: int, purpose: str) -> int:
    """A deterministic 64-bit sub-seed from ``(seed, purpose)``."""
    h = hashlib.sha256(f"{seed}||{purpose}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def index_at(base: int, step: int, bound: int) -> int:
    """A uniform-ish index in ``[0, bound)`` for shuffle ``step`` under ``base``.

    Uses the high-quality full-width bits of a per-step SHA-256 (not the low bits
    of an LCG), so the modulo draw is effectively unbiased for the small bounds a
    Fisher–Yates shuffle uses."""
    h = hashlib.sha256(f"{base}:{step}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") % bound


def seeded_shuffle(items: list, base: int) -> list:
    """A deterministic in-place Fisher–Yates shuffle under ``base`` (one seeded
    primitive so review/stratification/interleave can't diverge on how a seeded
    shuffle draws). Returns ``items`` for chaining."""
    for i in range(len(items) - 1, 0, -1):
        j = index_at(base, i, i + 1)
        items[i], items[j] = items[j], items[i]
    return items
