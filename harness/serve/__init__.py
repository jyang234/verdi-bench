"""Read-only live observer over one experiment [EVAL-13 AC-5, AC-6].

Presentation only: wraps the status aggregate, the ledger tail cursor, and the
EVAL-12 trial timeline behind loopback GET endpoints plus one self-contained
operator page. Appends nothing, mutates nothing, triggers nothing.
"""

from __future__ import annotations

from .server import DEFAULT_HOST, DEFAULT_PORT, make_server

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "make_server"]
