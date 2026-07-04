"""Real-container metering-proxy egress attribution [PRA-H4, REVIEW-D-P8-3].

Two levels of proof, both docker-marked:

* ``test_scan_attributes_real_container_log`` — a LIVE container writes the
  metering JSONL to a shared volume and the harness's ``_scan_proxy_log``
  attributes allow/deny to the right trial (ignoring other trials' lines). This
  proves the container→harness contract end-to-end without a registry pull (a
  FROM-scratch static emitter), so it runs here and in CI.

* ``test_reference_proxy_log_attributes_allow_and_deny`` — validates the ACTUAL
  shipped reference proxy (``deploy/metering-proxy/``) once it has been stood up
  and a trial driven through it: it is gated on ``VERDI_METERING_PROXY_LOG``
  pointing at that proxy's log. With ``VERDI_REQUIRE_PROXY`` set (the CI proxy
  job) a missing log FAILS rather than skips, so the job cannot green-pass by
  skipping — mirroring VERDI_REQUIRE_DOCKER/BROWSER.

Plus the fail-loud-on-missing-log guard, which needs no proxy at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.run.engines.harbor import HarborEngine, ProxyLogMissingError
from harness.run.types import ProxyConfig
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_EMIT_IMAGE = "verdi-proxy-emit:e2e"


class ProxyRequiredError(RuntimeError):
    """VERDI_REQUIRE_PROXY is set but no proxy log is configured [PRA-H4]."""


def _require_proxy_or_skip(reason: str):
    if os.environ.get("VERDI_REQUIRE_PROXY"):
        raise ProxyRequiredError(
            f"VERDI_REQUIRE_PROXY is set but {reason}; the real-proxy egress e2e "
            "must not green-pass by skipping. Stand up deploy/metering-proxy/ and "
            "set VERDI_METERING_PROXY_LOG, or unset VERDI_REQUIRE_PROXY [PRA-H4]."
        )
    pytest.skip(reason)


def _build_emitter(tmp_path: Path) -> bool:
    """A FROM-scratch container that writes the metering JSONL — a real container
    producing the exact schema the harness parses, with no registry pull."""
    gcc = shutil.which("gcc") or shutil.which("cc")
    if not gcc:
        return False
    (tmp_path / "emit.c").write_text(textwrap.dedent("""
        #include <stdio.h>
        int main(){
          FILE*f=fopen("/out/proxy.jsonl","w"); if(!f) return 1;
          fprintf(f,"{\\"trial\\":\\"e2e-trial\\",\\"host\\":\\"api.anthropic.com\\",\\"decision\\":\\"allow\\"}\\n");
          fprintf(f,"{\\"trial\\":\\"e2e-trial\\",\\"host\\":\\"evil.example\\",\\"decision\\":\\"deny\\"}\\n");
          fprintf(f,"{\\"trial\\":\\"other\\",\\"host\\":\\"evil.example\\",\\"decision\\":\\"deny\\"}\\n");
          fclose(f); return 0;
        }
    """), encoding="utf-8")
    if subprocess.run([gcc, "-static", "-O2", "-o", str(tmp_path / "emit"),
                       str(tmp_path / "emit.c")], capture_output=True).returncode != 0:
        return False
    (tmp_path / "Dockerfile").write_text(
        "FROM scratch\nCOPY emit /emit\nENTRYPOINT [\"/emit\"]\n", encoding="utf-8")
    return subprocess.run(["docker", "build", "-t", _EMIT_IMAGE, str(tmp_path)],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_scan_attributes_real_container_log(tmp_path):
    """A live container writes the metering JSONL; the harness attributes allow +
    deny to THIS trial and ignores another trial's lines."""
    if not _build_emitter(tmp_path):
        pytest.skip("cannot build a registry-free emitter image (no gcc / build failed)")
    outdir = tmp_path / "out"
    outdir.mkdir()
    subprocess.run(["docker", "run", "--rm", "-v", f"{outdir}:/out", _EMIT_IMAGE], check=True)
    log = outdir / "proxy.jsonl"
    assert log.exists()
    req = SimpleNamespace(
        trial_id="e2e-trial",
        proxy=ProxyConfig(proxy_url="http://e2e-trial@p:3128", log_path=str(log)),
    )
    attempts, violation, _cost = HarborEngine._scan_proxy_log(req)
    assert sorted(attempts) == ["api.anthropic.com", "evil.example"]  # this trial only
    assert violation is True  # the deny is flagged as an egress violation


def test_missing_proxy_log_fails_loud_on_real_path(tmp_path):
    """PRA-H4 on the real path too: a configured-but-absent log fails loud, never
    silently zero. Needs no proxy; runs wherever docker is available."""
    req = SimpleNamespace(
        trial_id="e2e-trial",
        proxy=ProxyConfig(proxy_url="http://p:3128", log_path=str(tmp_path / "nope.jsonl")),
    )
    with pytest.raises(ProxyLogMissingError):
        HarborEngine._scan_proxy_log(req)


def test_reference_proxy_log_attributes_allow_and_deny():
    """Validates the ACTUAL shipped reference proxy (deploy/metering-proxy/) once
    stood up: VERDI_METERING_PROXY_LOG must point at its log after a trial was
    driven through it. Fails (not skips) under VERDI_REQUIRE_PROXY [PRA-H4]."""
    proxy_log = os.environ.get("VERDI_METERING_PROXY_LOG")
    if not proxy_log:
        _require_proxy_or_skip("VERDI_METERING_PROXY_LOG is not set")
    log = Path(proxy_log)
    assert log.exists(), f"proxy log {log} not found — is the reference proxy up?"
    req = SimpleNamespace(
        trial_id="e2e-trial",
        proxy=ProxyConfig(proxy_url="http://e2e-trial@proxy:3128", log_path=str(log)),
    )
    attempts, violation, _cost = HarborEngine._scan_proxy_log(req)
    assert attempts, "no egress attributed to this trial"
    assert violation is True, "the denied host was not flagged"
