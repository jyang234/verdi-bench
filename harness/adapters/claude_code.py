"""claude-code adapter [EVAL-4 AC-2, Phase-0 spike 3].

Parses claude-code's native result log. Fields it exposes map to telemetry;
anything absent stays ``None`` and lands in ``telemetry_nulls`` — never estimated.
"""

from __future__ import annotations

from .base import Adapter, Telemetry
from .base import coerce_float as _float
from .base import coerce_int as _int


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
