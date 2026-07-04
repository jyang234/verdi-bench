"""Trajectory metrics — the closed, versioned forensic vocabulary [EVAL-11 AC-1].

``trajectory_metrics`` is a pure function of a *verified* EVAL-12
``TrajectoryRecord``: same record, byte-identical payload. The vocabulary is
exactly ``METRIC_IDS`` [EVAL-11-D001]; adding or changing a metric bumps
``FORENSICS_VOCABULARY_VERSION`` so findings from different vocabularies are
never merged silently. Unmeasurable inputs yield ``None``, never estimates
[§7.8, EVAL-4-D004] — in particular ``destructive_command_count`` is null
whenever any step's ``command`` is null (a v1 record, or an adapter that could
not surface the command), because counting only the measured commands would be
an estimate.

Deliberately imports no LLM client [AC-3, enforced by import-linter contract]
and no schema module: forensic metric ids live only here, so none of them can
ever validate as an EVAL-3 ``primary_metric`` [AC-5].
"""

from __future__ import annotations

import re
from typing import Optional

from ..run.trajectory import TrajectoryRecord, TrajectoryStep

FORENSICS_VOCABULARY_VERSION = 1

METRIC_IDS: tuple[str, ...] = (
    "step_distribution",
    "edit_test_cadence",
    "thrash_rate",
    "time_to_first_test",
    "error_recovery_latency",
    "destructive_command_count",
)

_STEP_KINDS: tuple[str, ...] = ("tool_call", "file_edit", "test_run", "message")

# Closed pattern list, part of vocabulary v1 [D001, D005]: a mechanical lookup
# over the step's measured command string, never an inference from context.
DESTRUCTIVE_COMMAND_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-[a-zA-Z]*[rf]",            # rm -r / rm -f and combinations
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\b[^|;&]*\s-[a-zA-Z]*f",
    r"\bgit\s+checkout\s+--\s",
    r"\bfind\b[^|;&]*\s-delete\b",
    r"\bshred\b",
    r"\btruncate\s+(-s\s*0|--size[= ]0)\b",
    r"\bmkfs\b",
    r"\bdd\b[^|;&]*\bof=",
)
_DESTRUCTIVE_RE = tuple(re.compile(p) for p in DESTRUCTIVE_COMMAND_PATTERNS)


def _edit_test_cadence(steps: list[TrajectoryStep]) -> int:
    loops = 0
    edited_since_test = False
    for s in steps:
        if s.kind == "file_edit":
            edited_since_test = True
        elif s.kind == "test_run":
            if edited_since_test:
                loops += 1
            edited_since_test = False
    return loops


def _thrash_rate(steps: list[TrajectoryStep]) -> Optional[float]:
    edits = [s for s in steps if s.kind == "file_edit"]
    if not edits or any(s.files_touched is None for s in edits):
        return None  # nothing edited, or an edit whose target is unmeasured
    seen: set[str] = set()
    re_edits = 0
    for s in edits:
        touched = set(s.files_touched or [])
        if touched & seen:
            re_edits += 1
        seen |= touched
    return re_edits / len(edits)


def _time_to_first_test(steps: list[TrajectoryStep]) -> Optional[float]:
    for s in steps:
        if s.kind == "test_run":
            return s.relative_ts  # None when the platform measured no ts
    return None  # no test ever ran — there is no time-to-first-test


def _error_recovery_latency(steps: list[TrajectoryStep]) -> Optional[float]:
    gaps: list[float] = []
    for i, s in enumerate(steps):
        if s.exit_code is None or s.exit_code == 0:
            continue
        recovery = next(
            (t for t in steps[i + 1 :] if t.exit_code == 0),
            None,
        )
        if recovery is None or s.relative_ts is None or recovery.relative_ts is None:
            return None  # an unrecovered or untimed failure makes the mean an estimate
        gaps.append(recovery.relative_ts - s.relative_ts)
    if not gaps:
        return None  # no measured failure — nothing to recover from
    return sum(gaps) / len(gaps)


def _destructive_command_count(steps: list[TrajectoryStep]) -> Optional[int]:
    if any(s.command is None for s in steps):
        return None  # an unmeasured command could be destructive [D005]
    return sum(
        1 for s in steps if any(rx.search(s.command or "") for rx in _DESTRUCTIVE_RE)
    )


def trajectory_metrics(record: TrajectoryRecord) -> dict:
    """The full vocabulary for one record; keys are exactly ``METRIC_IDS``."""
    steps = record.steps
    by_kind = {k: 0 for k in _STEP_KINDS}
    for s in steps:
        by_kind[s.kind] += 1
    return {
        "step_distribution": {"total": len(steps), "by_kind": by_kind},
        "edit_test_cadence": _edit_test_cadence(steps),
        "thrash_rate": _thrash_rate(steps),
        "time_to_first_test": _time_to_first_test(steps),
        "error_recovery_latency": _error_recovery_latency(steps),
        "destructive_command_count": _destructive_command_count(steps),
    }
