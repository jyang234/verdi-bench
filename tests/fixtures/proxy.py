"""Shared metering-proxy availability guard for the real-proxy e2e [PRA-H4].

``VERDI_REQUIRE_PROXY`` turns "skip when no proxy log is configured" into a
loud failure, so a CI proxy job that forgot to stand up the reference proxy
fails rather than silently skipping the egress-attribution proof — mirroring
the docker/browser guards (tests/fixtures/docker.py, tests/fixtures/browser.py).
"""

from __future__ import annotations

import os

import pytest


class ProxyRequiredError(RuntimeError):
    """VERDI_REQUIRE_PROXY is set but no proxy log is configured [PRA-H4]."""


def require_proxy_or_skip(reason: str):
    if os.environ.get("VERDI_REQUIRE_PROXY"):
        raise ProxyRequiredError(
            f"VERDI_REQUIRE_PROXY is set but {reason}; the real-proxy egress e2e "
            "must not green-pass by skipping. Stand up deploy/metering-proxy/ and "
            "set VERDI_METERING_PROXY_LOG, or unset VERDI_REQUIRE_PROXY [PRA-H4]."
        )
    pytest.skip(reason)
