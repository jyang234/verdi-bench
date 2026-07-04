"""PRA-H4 — the real-proxy egress e2e cannot green-pass by skipping.

VERDI_REQUIRE_PROXY turns "skip when no proxy log is configured" into a loud
failure, so a CI proxy job that forgot to stand up the reference proxy fails
rather than silently skipping the egress-attribution proof (the same anti-pattern
the audit flagged for the browser tier). Mirrors the docker/browser guards.
"""

from __future__ import annotations

import pytest

import tests.test_e2e_metering_proxy as proxy_mod


def test_require_proxy_raises_when_unconfigured(monkeypatch):
    monkeypatch.setenv("VERDI_REQUIRE_PROXY", "1")
    with pytest.raises(proxy_mod.ProxyRequiredError):
        proxy_mod._require_proxy_or_skip("no proxy log configured")


def test_without_require_proxy_absence_is_a_plain_skip(monkeypatch):
    monkeypatch.delenv("VERDI_REQUIRE_PROXY", raising=False)
    with pytest.raises(pytest.skip.Exception):
        proxy_mod._require_proxy_or_skip("no proxy log configured")
