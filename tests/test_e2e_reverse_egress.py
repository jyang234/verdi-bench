"""Live reverse-listener egress round-trip through a FAKE api.anthropic.com [RN-11].

Docker-marked, driving docker directly (the ``test_e2e_metering_proxy`` style — NOT
``MeteringProxy``). It proves the whole Option-5 path a proxy-defiant client needs:

* a fake upstream container aliased ``api.anthropic.com`` on the egress network
  serves TLS :443 (self-signed for that name), returns a canned JSON 400 with a
  distinctive marker, and records the request head it received;
* the packaged proxy runs with ``VERDI_REVERSE_PORTS=3129=api.anthropic.com`` and
  ``SSL_CERT_FILE`` pointed at the test CA, so it terminates plain HTTP from a
  metered client, attributes the trial from the ``/t/<trial>`` prefix, and
  originates VERIFIED TLS upstream;
* a metered client POSTs ``http://<proxy-ip>:3129/t/e2e-rev/v1/messages``.

Asserts the marker returns to the client, the upstream saw the PREFIX-STRIPPED path
with ``Host: api.anthropic.com`` and the api-key header, and the proxy's JSONL
attributes one allow line to the trial + upstream host. All containers/networks are
cleaned up in ``finally``.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from harness.hermetic import _proxy_container
from harness.hermetic.metering import PROXY_BASE_IMAGE
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_TRIAL = "e2e-rev"
_HOST = "api.anthropic.com"
_MARKER = "VERDI-REV-MARKER-42"
_API_KEY = "test-key-xyz"
_PROXY = "verdi-rev-proxy"
_UPSTREAM = "verdi-rev-upstream"
_CLIENT = "verdi-rev-client"
_METERED = "verdi-rev-metered"
_EGRESS = "verdi-rev-egress"

# A TLS upstream that records the request head and answers a marked JSON 400. Runs
# as root (a test fixture) so it may bind :443.
_UPSTREAM_SRC = textwrap.dedent(
    """
    import socket, ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("/verdi/cert.pem", "/verdi/key.pem")
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 443)); srv.listen(8)
    while True:
        raw, _ = srv.accept()
        try:
            conn = ctx.wrap_socket(raw, server_side=True)
        except Exception:
            continue
        data = b""
        while b"\\r\\n\\r\\n" not in data:
            c = conn.recv(4096)
            if not c: break
            data += c
        with open("/out/head.txt", "wb") as f:
            f.write(data)
        body = b'{"type":"error","marker":"%s"}'
        conn.sendall(b"HTTP/1.1 400 Bad Request\\r\\ncontent-type: application/json\\r\\n"
                     b"content-length: " + str(len(body)).encode() + b"\\r\\n"
                     b"connection: close\\r\\n\\r\\n" + body)
        conn.close()
    """
) % _MARKER

# A metered client that POSTs through the reverse listener with a /t/<trial> prefix.
_CLIENT_SRC = textwrap.dedent(
    """
    import socket
    body = b'{"model":"claude-3","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}'
    req = (b"POST /t/%s/v1/messages HTTP/1.1\\r\\n"
           b"Host: %s\\r\\n"
           b"x-api-key: %s\\r\\n"
           b"content-type: application/json\\r\\n"
           b"content-length: " + str(len(body)).encode() + b"\\r\\n\\r\\n" + body)
    s = socket.create_connection(("%s", 3129), timeout=15)
    s.sendall(req); s.settimeout(10); resp = b""
    while True:
        d = s.recv(4096)
        if not d: break
        resp += d
    s.close()
    assert b"%s" in resp, resp
    print("CLIENT-OK")
    """
)


def _rm(*names: str) -> None:
    for n in names:
        subprocess.run(["docker", "rm", "-f", n], capture_output=True)


def _rmnet(*names: str) -> None:
    for n in names:
        subprocess.run(["docker", "network", "rm", n], capture_output=True)


def _await_port(container: str, port: int) -> None:
    probe = (
        f"until python3 -c 'import socket; socket.create_connection((\"127.0.0.1\", {port}), 1)' "
        "2>/dev/null; do :; done"
    )
    subprocess.run(["docker", "exec", container, "sh", "-c", probe], timeout=30, check=True)


def _metered_ip(container: str) -> str:
    out = subprocess.run(
        ["docker", "inspect", "-f",
         '{{(index .NetworkSettings.Networks "%s").IPAddress}}' % _METERED, container],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


def _gen_cert_for_host(tmp_path: Path, hostname: str) -> tuple[Path, Path]:
    """Self-signed cert (DNS SAN = ``hostname``) + key — the fake upstream presents
    it and the proxy verifies it via SSL_CERT_FILE (the cert is its own CA)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2035, 1, 1, tzinfo=datetime.timezone.utc))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_reverse_egress_round_trip_through_fake_anthropic(tmp_path):
    certdir = tmp_path / "certs"
    certdir.mkdir()
    cert_file, key_file = _gen_cert_for_host(certdir, _HOST)
    outdir = tmp_path / "out"
    outdir.mkdir()
    logdir = tmp_path / "metering"
    logdir.mkdir()
    log = logdir / "verdi.jsonl"
    proxy_src = Path(_proxy_container.__file__)

    _rm(_UPSTREAM, _CLIENT, _PROXY)
    _rmnet(_METERED, _EGRESS)
    try:
        subprocess.run(["docker", "network", "create", "--internal", _METERED], check=True,
                       capture_output=True)
        subprocess.run(["docker", "network", "create", _EGRESS], check=True, capture_output=True)

        # fake api.anthropic.com on the egress network (aliased), serving TLS :443
        subprocess.run(
            ["docker", "run", "-d", "--name", _UPSTREAM,
             "--network", _EGRESS, "--network-alias", _HOST,
             "-v", f"{certdir}:/verdi:ro", "-v", f"{outdir}:/out",
             PROXY_BASE_IMAGE, "python3", "-c", _UPSTREAM_SRC],
            check=True, capture_output=True,
        )
        _await_port(_UPSTREAM, 443)

        # the packaged proxy with a reverse listener for the host + the test CA
        subprocess.run(
            ["docker", "run", "-d", "--name", _PROXY, "--network", _METERED,
             "--env", f"VERDI_PROXY_ALLOW={_HOST}",
             "--env", f"VERDI_REVERSE_PORTS=3129={_HOST}",
             "--env", "PROXY_LOG=/var/log/verdi/verdi.jsonl",
             "--env", "SSL_CERT_FILE=/verdi/test-ca.pem",
             "-v", f"{proxy_src}:/verdi/proxy.py:ro",
             "-v", f"{cert_file}:/verdi/test-ca.pem:ro",
             "-v", f"{logdir}:/var/log/verdi",
             PROXY_BASE_IMAGE, "python3", "/verdi/proxy.py"],
            check=True, capture_output=True,
        )
        # the proxy bridges to egress to reach the aliased upstream
        subprocess.run(["docker", "network", "connect", _EGRESS, _PROXY], check=True,
                       capture_output=True)
        _await_port(_PROXY, 3129)

        proxy_ip = _metered_ip(_PROXY)
        assert proxy_ip, "proxy has no metered-network IP"

        client_src = _CLIENT_SRC % (_TRIAL, _HOST, _API_KEY, proxy_ip, _MARKER)
        proc = subprocess.run(
            ["docker", "run", "--rm", "--name", _CLIENT, "--network", _METERED,
             PROXY_BASE_IMAGE, "python3", "-c", client_src],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"client failed: {proc.stdout}\n{proc.stderr}"
        assert "CLIENT-OK" in proc.stdout  # the marker round-tripped back

        # the upstream saw the prefix-stripped path, rewritten Host, and the api key
        # (read_bytes, not read_text — text mode would translate the \r\n framing away)
        head = (outdir / "head.txt").read_bytes().decode("latin1")
        lines = head.split("\r\n")
        assert lines[0] == "POST /v1/messages HTTP/1.1", lines
        assert f"Host: {_HOST}" in lines, lines
        assert f"x-api-key: {_API_KEY}" in lines, lines

        # the proxy attributes one allow line to the trial + upstream host
        records = [
            json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        assert {"trial": _TRIAL, "host": _HOST, "decision": "allow"} in records, records
    finally:
        _rm(_UPSTREAM, _CLIENT, _PROXY)
        _rmnet(_METERED, _EGRESS)
