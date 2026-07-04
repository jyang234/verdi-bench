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
    infra_hosts: list[str] | None = None,
) -> ProxyConfig:
    return ProxyConfig(
        allowlist=list(allowlist if allowlist is not None else DEFAULT_MODEL_API_HOSTS),
        proxy_url=proxy_url,
        log_path=log_path,
        infra_hosts=list(infra_hosts or []),
    )


def arm_declared_hosts(arm) -> list[str]:
    """Flatten one arm's ``model_hosts`` values — the single flattening the
    spec-derived allowlist and per-trial attestation share, so "allowed" and
    "attributable" cannot drift [EVAL-13 AC-6]."""
    return [h for declared in arm.model_hosts.values() for h in declared]


def spec_allowlist(spec) -> list[str]:
    """The allowlist a spec pre-registers [EVAL-13 AC-6, D003]: the union of
    every arm's ``model_hosts`` values and the experiment's ``infra_hosts``.
    Empty when the spec declares no hosts (pre-EVAL-13 posture — the runtime
    config keeps supplying the allowlist)."""
    hosts: set[str] = set(spec.infra_hosts)
    for arm in spec.arms:
        hosts.update(arm_declared_hosts(arm))
    return sorted(hosts)


def undeclared_model_egress(proxy: ProxyConfig, arm, attempts: list[str]) -> list[str]:
    """ALLOWED egress hosts attributable to neither this arm's declared
    ``model_hosts`` nor the shared ``infra_hosts`` [EVAL-13 AC-6, D003].

    Advisory only — the caller attaches the result as a flag; it never gates
    and never fails the trial. Empty when the arm declared no ``model_hosts``:
    an undeclared arm has nothing to attest against, the honest absent state.
    Denied hosts are already ``egress_violation``; this catches the
    allowed-but-unattributable case, e.g. an arm reaching the OTHER arm's
    declared model endpoint."""
    if not arm.model_hosts or not attempts:
        return []
    attributable = [*proxy.infra_hosts, *arm_declared_hosts(arm)]
    return sorted({
        h for h in attempts
        if proxy.is_allowed(h) and not proxy.host_matches(h, attributable)
    })
