"""Adapter registry: agent platform → telemetry adapter."""

from __future__ import annotations

from .base import Adapter, Outcome, Provenance, Quotas, Telemetry, TrialRecord
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .generic import GenericAdapter, GenericLogError

_ADAPTERS: dict[str, Adapter] = {
    a.platform: a for a in (ClaudeCodeAdapter(), CodexAdapter(), GenericAdapter())
}


class UnknownPlatformError(KeyError):
    pass


def known_platforms() -> list[str]:
    """The registered adapter platforms — the set of runnable ``arm.platform``
    values. Public so plan-time validation refuses an unrunnable platform
    before any spend, instead of it surfacing mid-run as per-trial
    ``trial_infra_failed(unknown_platform)`` [RN-15]."""
    return sorted(_ADAPTERS)


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
    "GenericAdapter",
    "GenericLogError",
    "Outcome",
    "Provenance",
    "Quotas",
    "Telemetry",
    "TrialRecord",
    "get_adapter",
    "known_platforms",
    "UnknownPlatformError",
]
