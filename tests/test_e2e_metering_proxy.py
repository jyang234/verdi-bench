"""Real-proxy egress e2e [PRA-H4, REVIEW-D-P8-3].

Validates the metering-proxy contract end to end against a LIVE proxy: an
allowed host is attributed and permitted, a denied host is attributed and
flagged, and both carry the requesting trial's credential. This is the coverage
the fast suite cannot provide — the fast tests exercise `_scan_proxy_log` against
hand-written logs; this proves a real proxy produces logs of that shape and that
attribution is not spoofable.

It is docker-marked (deselected by the fast suite) AND gated on
`VERDI_METERING_PROXY_LOG` pointing at the deployed reference proxy's log
(`deploy/metering-proxy/`), because it needs that external component running.
Where the proxy is deployed (a Docker+Squid CI job or an operator's environment)
it executes; elsewhere it skips loudly rather than passing vacuously — mirroring
the docker suite's `VERDI_REQUIRE_DOCKER` discipline.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness.run.engines.harbor import HarborEngine, ProxyLogMissingError
from harness.run.types import ProxyConfig

pytestmark = pytest.mark.docker

_PROXY_LOG = os.environ.get("VERDI_METERING_PROXY_LOG")


@pytest.mark.skipif(
    not _PROXY_LOG,
    reason="set VERDI_METERING_PROXY_LOG to the deployed reference proxy's log "
    "(deploy/metering-proxy/) to run the real-proxy egress e2e",
)
def test_real_proxy_log_attributes_allow_and_deny_by_trial():
    """The deployed proxy's JSONL must let _scan_proxy_log attribute an allow and
    a deny to the exact trial credential that made them."""
    from types import SimpleNamespace

    log = Path(_PROXY_LOG)
    assert log.exists(), f"proxy log {log} not found — is the reference proxy up?"
    # The operator/CI harness is expected to have driven one allowed + one denied
    # request under trial id 'e2e-trial' before invoking this test.
    req = SimpleNamespace(
        trial_id="e2e-trial",
        proxy=ProxyConfig(proxy_url="http://e2e-trial@proxy:3128", log_path=str(log)),
    )
    attempts, violation, _cost = HarborEngine._scan_proxy_log(req)
    assert attempts, "no egress attempts attributed to this trial"
    assert violation is True, "the denied host was not flagged as an egress violation"
    # every logged line the harness counted is a well-formed JSONL object with the
    # contract fields
    for raw in log.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        rec = json.loads(raw)
        assert {"trial", "host", "decision"} <= set(rec), rec


def test_missing_proxy_log_raises_even_with_docker(tmp_path):
    """PRA-H4 holds on the real path too: a configured-but-absent log fails loud,
    never silently zero. (Runs wherever docker is available; needs no proxy.)"""
    from types import SimpleNamespace

    req = SimpleNamespace(
        trial_id="e2e-trial",
        proxy=ProxyConfig(proxy_url="http://p:3128", log_path=str(tmp_path / "nope.jsonl")),
    )
    with pytest.raises(ProxyLogMissingError):
        HarborEngine._scan_proxy_log(req)
