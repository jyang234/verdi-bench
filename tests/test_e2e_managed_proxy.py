"""Live managed metering proxy — end-to-end [refactor 04 §1].

Docker-marked. Stands the managed proxy up via ``MeteringProxy.managed(...)``, then
drives a container on the metered network through it (with a trial-id credential)
to an ALLOWED host and a DENIED host, and asserts the proxy's JSONL attributes
both to the trial with the right decision. On teardown, zero networks/containers
remain.

Unlike the FROM-scratch gcc emitters elsewhere, this uses the pinned
``python:3.12-alpine`` (a registry pull, not a static build), so it runs anywhere
docker is available — it is not linux-gcc-gated.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from harness.hermetic.metering import (
    EGRESS_NETWORK,
    MANAGED_PROXY_NAME,
    METERED_NETWORK,
    PROXY_BASE_IMAGE,
    MeteringProxy,
)
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_TRIAL = "e2e-managed-trial"
_UPSTREAM = "verdi-e2e-upstream"
_CLIENT = "verdi-e2e-client"
_ALLOWED_HOST = "allowed.internal"  # a docker network alias on the egress net
_UPSTREAM_PORT = 8443

# A container on egress that just accepts TCP — enough for the proxy's CONNECT to
# succeed and log ``allow``.
_UPSTREAM_SRC = textwrap.dedent(
    """
    import socket
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", %d))
    s.listen(16)
    while True:
        c, _ = s.accept()
        c.close()
    """
) % _UPSTREAM_PORT

# A container on the metered net that CONNECTs through the proxy to one allowed
# and one denied host, presenting the trial id as basic-auth userinfo (frozen
# per-trial credential contract).
_CLIENT_SRC = textwrap.dedent(
    """
    import base64, socket
    def go(target, trial):
        s = socket.create_connection(("%s", 3128), timeout=10)
        cred = base64.b64encode((trial + ":").encode()).decode()
        req = ("CONNECT " + target + " HTTP/1.1\\r\\nHost: " + target
               + "\\r\\nProxy-Authorization: Basic " + cred + "\\r\\n\\r\\n")
        s.sendall(req.encode())
        s.recv(1024)
        s.close()
    go("%s:%d", "%s")
    go("evil.example:443", "%s")
    """
) % (MANAGED_PROXY_NAME, _ALLOWED_HOST, _UPSTREAM_PORT, _TRIAL, _TRIAL)


def _rm(*names: str) -> None:
    for n in names:
        subprocess.run(["docker", "rm", "-f", n], capture_output=True)


def _network_exists(name: str) -> bool:
    return subprocess.run(["docker", "network", "inspect", name], capture_output=True).returncode == 0


def _container_exists(name: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    return name in out.stdout.split()


def _await_port(container: str, port: int) -> None:
    """Block until ``container`` accepts on ``port`` — a probe, not a fixed wait."""
    probe = (
        f"until python3 -c 'import socket; socket.create_connection((\"127.0.0.1\", {port}), 1)' "
        "2>/dev/null; do :; done"
    )
    subprocess.run(["docker", "exec", container, "sh", "-c", probe], timeout=20, check=True)


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_managed_proxy_stands_up_meters_and_tears_down(tmp_path):
    # custom basename on purpose: pins the F1 fail-open fix (the proxy must write
    # the operator's exact filename, not verdi.jsonl beside an empty custom file)
    log = tmp_path / "metering" / "custom-egress.jsonl"
    _rm(_UPSTREAM, _CLIENT, MANAGED_PROXY_NAME)
    try:
        with MeteringProxy.managed([_ALLOWED_HOST], log_path=log) as cfg:
            # readiness was probed (not slept): the proxy is addressable now.
            assert cfg.proxy_url == f"http://{MANAGED_PROXY_NAME}:3128"
            assert cfg.log_path == str(log)

            # an upstream the proxy can reach, aliased on the egress network so the
            # ALLOWED CONNECT actually completes (and logs ``allow``, not a 502 deny)
            subprocess.run(
                ["docker", "run", "-d", "--name", _UPSTREAM,
                 "--network", EGRESS_NETWORK, "--network-alias", _ALLOWED_HOST,
                 PROXY_BASE_IMAGE, "python3", "-c", _UPSTREAM_SRC],
                check=True, capture_output=True,
            )
            _await_port(_UPSTREAM, _UPSTREAM_PORT)

            # a trial-like container on the metered net drives one allow + one deny
            proc = subprocess.run(
                ["docker", "run", "--rm", "--name", _CLIENT, "--network", METERED_NETWORK,
                 PROXY_BASE_IMAGE, "python3", "-c", _CLIENT_SRC],
                capture_output=True, text=True, timeout=60,
            )
            assert proc.returncode == 0, f"client failed: {proc.stderr}"

            records = [
                json.loads(line)
                for line in Path(log).read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            mine = [r for r in records if r.get("trial") == _TRIAL]
            allow = {r["host"] for r in mine if r.get("decision") == "allow"}
            deny = {r["host"] for r in mine if r.get("decision") == "deny"}
            assert _ALLOWED_HOST in allow, f"allowed host not attributed as allow: {mine}"
            assert "evil.example" in deny, f"denied host not attributed as deny: {mine}"

            # remove the egress-attached upstream BEFORE teardown removes that network
            _rm(_UPSTREAM)

        # teardown left nothing behind
        assert not _container_exists(MANAGED_PROXY_NAME)
        assert not _network_exists(METERED_NETWORK)
        assert not _network_exists(EGRESS_NETWORK)
    finally:
        _rm(_UPSTREAM, _CLIENT, MANAGED_PROXY_NAME)
        subprocess.run(["docker", "network", "rm", EGRESS_NETWORK], capture_output=True)
        subprocess.run(["docker", "network", "rm", METERED_NETWORK], capture_output=True)
