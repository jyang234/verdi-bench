"""Experiment lifecycle status — pure reads for observers [EVAL-13 AC-3, AC-4].

The one place that answers "where is this experiment in its lifecycle?" from
the ledger, the locked spec, and the run heartbeat — appending nothing,
mutating nothing. ``bench status`` and ``bench serve`` are its only consumers;
no gating stage reads it.
"""

from __future__ import annotations

from .aggregate import STATUS_SCHEMA_VERSION, compute_status

__all__ = ["STATUS_SCHEMA_VERSION", "compute_status"]
