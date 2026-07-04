"""Shared RFC 3339 date parsing [EVAL-10 AC-1].

The one implementation every date-bearing field uses — ``Arm.training_cutoff``,
``TaskEntry.created_at``, and the contamination dating channel — so a value
accepted at spec/manifest load can never parse differently (or fail) at
analysis time. A naive value is pinned to UTC so mixed naive/aware comparisons
are total instead of a mid-comparison ``TypeError``.
"""

from __future__ import annotations

from datetime import datetime, timezone


class Rfc3339Error(ValueError):
    """A date that should be RFC 3339 does not parse — refused loudly, naming
    the field, never silently degraded [fail-loudly]."""


def parse_rfc3339(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as e:
        raise Rfc3339Error(
            f"{field} {value!r} is not an RFC 3339 date/timestamp: {e}"
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
