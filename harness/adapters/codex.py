"""codex adapter [EVAL-4 AC-2, Phase-0 spike 3].

Codex's native log exposes token counts and elapsed time but not per-call cache
tokens or a cost figure; those stay ``None`` (→ ``telemetry_nulls``), never
proxy-estimated [D004]. This asymmetry with claude-code is exactly why cross-arm
telemetry comparisons only run on fields both arms measured [EVAL-6 AC-7].
"""

from __future__ import annotations

from .base import Adapter, Telemetry
from .base import coerce_float as _float
from .base import coerce_int as _int


class CodexAdapter(Adapter):
    platform = "codex"

    def normalize(self, native_log: dict) -> Telemetry:
        usage = native_log.get("token_usage") or {}
        return Telemetry(
            tokens_in=_int(usage.get("prompt_tokens")),
            tokens_out=_int(usage.get("completion_tokens")),
            tokens_cache=None,  # codex does not report cache tokens
            cost=None,          # codex does not report cost
            wall_time_s=_float(native_log.get("elapsed_seconds")),
            tool_calls=_int(native_log.get("tool_calls")),
        )
