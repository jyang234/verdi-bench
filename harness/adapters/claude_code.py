"""claude-code adapter [EVAL-4 AC-2, Phase-0 spike 3].

Parses claude-code's native result log. Fields it exposes map to telemetry;
anything absent stays ``None`` and lands in ``telemetry_nulls`` — never estimated.
"""

from __future__ import annotations

from typing import Optional

from ..run.trajectory import TrajectoryStep
from .base import Adapter, Telemetry
from .base import coerce_float as _float
from .base import coerce_int as _int

# Closed tool-name table: tools whose invocation IS a file edit. A mechanical
# lookup, not an inference — an unknown tool stays a generic tool_call.
_FILE_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


class ClaudeCodeAdapter(Adapter):
    platform = "claude_code"

    def normalize(self, native_log: dict) -> Telemetry:
        usage = native_log.get("usage") or {}
        duration_ms = native_log.get("duration_ms")
        tool_calls = native_log.get("tool_use_count")
        if tool_calls is None and isinstance(native_log.get("messages"), list):
            # fall back to counting tool_use blocks in the transcript
            tool_calls = sum(
                1
                for m in native_log["messages"]
                for c in (m.get("content") or [])
                if isinstance(c, dict) and c.get("type") == "tool_use"
            )
        return Telemetry(
            tokens_in=_int(usage.get("input_tokens")),
            tokens_out=_int(usage.get("output_tokens")),
            tokens_cache=_int(usage.get("cache_read_input_tokens")),
            cost=_float(native_log.get("total_cost_usd")),
            wall_time_s=(_float(duration_ms) / 1000.0) if duration_ms is not None else None,
            tool_calls=_int(tool_calls),
        )

    def normalize_trajectory(self, native_log: dict) -> Optional[list[TrajectoryStep]]:
        """Message stream → steps [EVAL-12 AC-1].

        claude-code's result log exposes ordered content blocks but no per-step
        timings, token splits, costs, or exit codes — those stay null [D004].
        A ``test_run`` is not natively labeled either, so it is never emitted
        here (labeling one from command text would be estimation, not
        measurement). No ``messages`` key at all ⇒ no trajectory (``None``),
        the honest absent state [AC-2].
        """
        messages = native_log.get("messages")
        if not isinstance(messages, list):
            return None
        steps: list[TrajectoryStep] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            for c in m.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    steps.append(TrajectoryStep(kind="message"))
                elif c.get("type") == "tool_use":
                    name = c.get("name")
                    file_path = (c.get("input") or {}).get("file_path")
                    steps.append(
                        TrajectoryStep(
                            kind="file_edit" if name in _FILE_EDIT_TOOLS else "tool_call",
                            files_touched=[str(file_path)] if file_path else None,
                        )
                    )
        return steps
