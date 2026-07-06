"""``harness.hermetic`` — one owner for Docker mechanics [refactor 04 §1].

All docker argv construction, daemon probing, network lifecycle, and the managed
metering-proxy lifecycle live here. The package is a **leaf**: it imports no
engine and never names harbor, so the harbor-confinement import contract and the
AST seam sweep (tests/test_eval4_seam.py) both stay green with ``hermetic`` as a
source.
"""

from __future__ import annotations

from harness.hermetic.docker import (
    DAEMON_ERROR_EXIT,
    TIMEOUT_EXIT,
    DockerClient,
    HardenedCommand,
)
from harness.hermetic.metering import (
    MANAGED_PROXY_NAME,
    PROXY_BASE_IMAGE,
    MeteringProxy,
    MeteringProxyError,
    teardown_managed,
)
from harness.hermetic.network import (
    EGRESS_NETWORK,
    METERED_NETWORK,
    NetworkError,
    connect_network,
    create_network,
    ensure_metered_network,
    remove_network,
)

__all__ = [
    "DockerClient",
    "HardenedCommand",
    "DAEMON_ERROR_EXIT",
    "TIMEOUT_EXIT",
    "METERED_NETWORK",
    "EGRESS_NETWORK",
    "NetworkError",
    "ensure_metered_network",
    "create_network",
    "connect_network",
    "remove_network",
    "MeteringProxy",
    "MeteringProxyError",
    "teardown_managed",
    "MANAGED_PROXY_NAME",
    "PROXY_BASE_IMAGE",
]
