"""Run liveness heartbeat sidecar [EVAL-13 AC-1, D001].

Operational telemetry beside the ledger, never in it: the scheduler rewrites
``run.heartbeat.json`` on every state change so a live observer can see the
in-flight cell, progress counters, and spend *between* ledger events — the one
thing the completion-only ledger cannot show. It follows the ``run.config.yaml``
precedent (operational file, outside the hash chain), not the ledger-event
precedent: no gating stage reads it, and no reader may refuse an experiment
over its absence.

Write discipline: whole-document write-temp + ``os.replace``, so a concurrent
reader can never observe a torn file. No fsync — the file is ephemeral liveness,
not evidence; atomicity comes from rename semantics, durability is deliberately
not promised. A crashed run leaves a stale ``running`` document; readers surface
it verbatim (with its ``ts``) and let the presentation layer judge staleness —
the harness never guesses at liveness it did not observe.

Write failures propagate — the sidecar sits beside the ledger and shares its
fail-loud fate; a swallowed heartbeat error would be exactly the silent
degradation this instrument refuses.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..ledger.events import EventContext

HEARTBEAT_FILENAME = "run.heartbeat.json"
HEARTBEAT_SCHEMA_VERSION = 1

STATE_RUNNING = "running"
STATE_FINISHED = "finished"
STATE_STOPPED_COST_CEILING = "stopped_cost_ceiling"


@dataclass
class RunHeartbeat:
    """Maintains the sidecar document across one ``schedule`` invocation.

    Timestamps flow through the injected :class:`EventContext` clock — the same
    seam every ledger event uses — so tests get deterministic heartbeats and
    production gets wall-clock UTC.
    """

    path: Path
    ctx: EventContext
    planned: int
    ceiling: float
    cells_done: int = 0
    infra_failures: int = 0
    accumulated: float = 0.0
    in_flight: Optional[dict] = field(default=None)
    state: str = STATE_RUNNING

    def start(self, *, cells_done: int, accumulated: float) -> None:
        """First write of the run: resume-aware counters, state ``running``."""
        self.cells_done = cells_done
        self.accumulated = accumulated
        self.state = STATE_RUNNING
        self._write()

    def trial_started(
        self, *, task_id: str, arm: str, repetition: int, trial_id: str, attempt: int
    ) -> None:
        """An attempt is executing: publish the in-flight cell (attempt number
        included, so infra re-runs are visible as attempts 2, 3, …)."""
        self.in_flight = {
            "task_id": task_id,
            "arm": arm,
            "repetition": repetition,
            "trial_id": trial_id,
            "attempt": attempt,
            "started_ts": self.ctx.clock(),
        }
        self._write()

    def trial_completed(self, *, accumulated: float) -> None:
        """A trial event landed (completed or timeout): count the cell done."""
        self.cells_done += 1
        self.accumulated = accumulated
        self.in_flight = None
        self._write()

    def infra_failed(self, *, accumulated: Optional[float] = None) -> None:
        """A ``trial_infra_failed`` landed: count it, clear any in-flight cell.
        ``accumulated`` is passed only when the failed attempt carried spend
        (the infra-rerun path); a cell that never started changes no spend."""
        self.infra_failures += 1
        if accumulated is not None:
            self.accumulated = accumulated
        self.in_flight = None
        self._write()

    def finish(self, *, stopped_cost_ceiling: bool, accumulated: float) -> None:
        """Terminal write for a loop that exited normally. A crash never reaches
        here — the stale ``running`` document is the documented crash artifact."""
        self.accumulated = accumulated
        self.in_flight = None
        self.state = (
            STATE_STOPPED_COST_CEILING if stopped_cost_ceiling else STATE_FINISHED
        )
        self._write()

    def _write(self) -> None:
        doc = {
            "schema_version": HEARTBEAT_SCHEMA_VERSION,
            "experiment_id": self.ctx.experiment_id,
            "state": self.state,
            "ts": self.ctx.clock(),
            "pid": os.getpid(),
            "cells": {
                "planned": self.planned,
                "done": self.cells_done,
                "infra_failures": self.infra_failures,
            },
            "spend": {"accumulated": self.accumulated, "ceiling": self.ceiling},
            "in_flight": self.in_flight,
        }
        tmp = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        tmp.write_text(
            json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)


def read_heartbeat(path) -> Optional[dict]:
    """Parse the sidecar; ``None`` when absent (a tolerated state — pre-EVAL-13
    experiment or a run that never started). Corrupt content raises: the atomic
    writer cannot produce a torn file, so malformed JSON means something else
    wrote here and must be surfaced, not smoothed over."""
    path = Path(path)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"heartbeat {path} is not valid JSON ({e}); the atomic writer never "
            "leaves torn documents — refusing to guess at foreign content"
        ) from e
