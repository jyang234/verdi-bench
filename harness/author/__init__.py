"""Experiment authoring — browser pre-registration [EVAL-17].

The mutating counterpart to the read-only observer, deliberately its own
subsystem and verb: previews are pure reads over saved draft files, and the
whole surface performs exactly one ledgered operation — the lock, through
``plan.lock.lock_experiment`` verbatim. Drafts are plain directories under
the workspace root (D004); a directory becomes an experiment when its
genesis event lands, and never changes here afterwards.
"""

from __future__ import annotations

from .server import DEFAULT_AUTHOR_PORT, make_author_server

__all__ = ["DEFAULT_AUTHOR_PORT", "make_author_server"]
