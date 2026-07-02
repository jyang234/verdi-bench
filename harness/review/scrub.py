"""Blind scrub for review packets [EVAL-7 §M2, AC-1].

A **thin wrapper** over ``harness/blind/core.py`` — the single blinding codepath
the judge packets also use (master plan §7.4). One scrub implementation, one set
of canaries to test. Scrub is fail-closed: if any identity canary survives a
scrub, packet generation is **blocked** (never ship a packet that could leak
which arm produced an artifact) — consistent with EVAL-2's never-send.
"""

from __future__ import annotations

from ..blind.core import identity_pattern_list


class ScrubError(RuntimeError):
    """An identity canary survived scrubbing — packet generation is blocked."""


def blind_scrub(text: str, canaries: list[str] | None = None) -> str:
    """Scrub identity canaries + per-experiment literals from ``text``."""
    scrubbed, _ = identity_pattern_list(extra_literals=canaries).scrub(text)
    return scrubbed


def assert_identity_free(text: str, canaries: list[str] | None = None) -> None:
    """Raise :class:`ScrubError` if any identity marker remains [fail closed]."""
    patterns = identity_pattern_list(extra_literals=canaries)
    hits = patterns.scan(text)
    if hits:
        raise ScrubError(
            f"identity marker {hits[0].text!r} survived scrub; packet blocked [AC-1]"
        )
