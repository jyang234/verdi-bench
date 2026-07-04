"""Cutoff dating: deterministic contamination tri-state per (task, arm) [EVAL-10 AC-1].

The cheapest channel, strongest when it applies: a task created strictly after
an arm model's training cutoff cannot have been memorized by that model.
``unknown`` is a first-class honest state (§7.8 cross-vendor honesty) — a
missing date never launders into clean — and a positive detection (AC-3 canary,
AC-4 overlap) outranks dating in the flagged direction. Pure by construction:
no clock, no ledger, no I/O. Date parsing is the shared
:mod:`harness.schema.dates` implementation, the same one the schema validators
run at load time, so a date accepted there cannot fail here.
"""

from __future__ import annotations

from enum import Enum

from ..schema.dates import Rfc3339Error, parse_rfc3339

# The dating channel's refusal is the shared parse refusal — one grammar, one
# error; re-exported under the story-local name the tests and callers use.
DatingError = Rfc3339Error

__all__ = ["ContaminationStatus", "DatingError", "cutoff_status", "parse_rfc3339"]


class ContaminationStatus(str, Enum):
    """Per-(task, arm) contamination tri-state [AC-1]."""

    CLEAN_BY_DATE = "clean_by_date"
    UNKNOWN = "unknown"
    FLAGGED = "flagged"


def cutoff_status(
    created_at: str | None,
    training_cutoff: str | None,
    *,
    flagged: bool = False,
) -> ContaminationStatus:
    """The deterministic tri-state for one (task, arm) pair [AC-1].

    ``flagged`` (a positive AC-3/AC-4 detection) outranks dating: a detection
    on a nominally post-cutoff task means the dates are wrong, not the
    evidence. ``clean_by_date`` requires both dates present and creation
    *strictly* after the cutoff; anything else — either date absent, or
    creation at/before the cutoff — is ``unknown``, never coerced to clean.
    """
    if flagged:
        return ContaminationStatus.FLAGGED
    if created_at is None or training_cutoff is None:
        return ContaminationStatus.UNKNOWN
    created = parse_rfc3339(created_at, field="created_at")
    cutoff = parse_rfc3339(training_cutoff, field="training_cutoff")
    if created > cutoff:
        return ContaminationStatus.CLEAN_BY_DATE
    return ContaminationStatus.UNKNOWN
