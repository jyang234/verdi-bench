"""Adapter registry: agent platform → telemetry adapter."""

from __future__ import annotations

from .base import Adapter, Outcome, Provenance, Quotas, Telemetry, TrialRecord
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter

_ADAPTERS: dict[str, Adapter] = {
    a.platform: a for a in (ClaudeCodeAdapter(), CodexAdapter())
}


class UnknownPlatformError(KeyError):
    pass


def get_adapter(platform: str) -> Adapter:
    try:
        return _ADAPTERS[platform]
    except KeyError:
        raise UnknownPlatformError(
            f"no adapter for platform {platform!r}; known: {sorted(_ADAPTERS)}"
        ) from None


__all__ = [
    "Adapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "Outcome",
    "Provenance",
    "Quotas",
    "Telemetry",
    "TrialRecord",
    "get_adapter",
    "UnknownPlatformError",
]
