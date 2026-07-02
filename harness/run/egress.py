"""Metering-proxy egress configuration [EVAL-4 §M2, AC-3, D001].

The existing Squid/devcontainer proxy architecture drops in as the metering
proxy; this module just produces the :class:`ProxyConfig` the engine wires in.
Allowlist = model-API hosts; every other attempt is logged and flagged.
"""

from __future__ import annotations

from .types import ProxyConfig

# Model-API hosts reachable through the metering proxy. Everything else is a
# violation (flagged, never silently allowed).
DEFAULT_MODEL_API_HOSTS = [
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.x.ai",
]


def proxy_config(
    allowlist: list[str] | None = None,
    *,
    proxy_url: str | None = None,
    log_path: str | None = None,
) -> ProxyConfig:
    return ProxyConfig(
        allowlist=list(allowlist if allowlist is not None else DEFAULT_MODEL_API_HOSTS),
        proxy_url=proxy_url,
        log_path=log_path,
    )
