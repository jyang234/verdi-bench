"""Hermetic relay tests for the packaged metering CONNECT proxy [refactor 04 §1].

The proxy script (`harness/hermetic/_proxy_container.py`) normally runs inside
the pinned container and is exercised end-to-end by the docker-marked e2e tier;
these tests pin its RELAY correctness at the socket level, no daemon needed.

The pipelined-bytes case reproduces the 2026-07-07 pilot failure: Bun's fetch
(the pinned `claude` CLI runtime) sends the TLS ClientHello optimistically in
the same segment as the CONNECT header. A proxy that discards whatever trailed
the header block leaves the tunnel permanently dead — the client retries until
its budget exhausts and every trial dies with ConnectionRefused/
FailedToOpenSocket while the metering log shows only healthy "allow" lines.

The reverse-listener cases [RN-11] pin the additive plain-HTTP terminator that
the same pinned `claude` CLI needs: it ignores HTTP(S)_PROXY entirely
(anthropics/claude-code#14165) but honors ``ANTHROPIC_BASE_URL`` over plain HTTP,
so harbor points that base URL at a reverse listener. The listener attributes the
trial from a ``/t/<trial-id>`` path prefix, originates verified TLS to the real
upstream, and splices bytes both ways — answering Bun's ``HEAD`` preflight locally
and streaming the upstream's response UNBUFFERED (the SSL ``pending()`` drain is
what keeps a select loop from stalling mid-response). Every request emits one
allow/deny line in the frozen JSONL shape.
"""

from __future__ import annotations

import base64
import datetime
import ipaddress
import json
import socket
import ssl
import threading
from pathlib import Path

import pytest

from harness.hermetic import _proxy_container as proxy


def _auth_header(trial: str) -> str:
    token = base64.b64encode(f"{trial}:".encode()).decode()
    return f"Proxy-Authorization: Basic {token}"


def _connect_head(port: int, trial: str = "t1") -> bytes:
    return (
        f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"{_auth_header(trial)}\r\n\r\n"
    ).encode()


@pytest.fixture()
def upstream():
    """A local TCP upstream that records every byte it receives."""
    received = bytearray()
    done = threading.Event()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(5)
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                received.extend(data)
                if b"LATE" in received:
                    # echo something back so the relay proves both directions
                    conn.sendall(b"PONG")
        except TimeoutError:
            pass
        finally:
            conn.close()
            done.set()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield port, received, done
    finally:
        srv.close()


@pytest.fixture()
def allow_local(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy, "ALLOW", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(proxy, "LOG", str(tmp_path / "verdi.jsonl"))


def _run_handle(client_side: socket.socket) -> threading.Thread:
    t = threading.Thread(target=proxy.handle, args=(client_side,), daemon=True)
    t.start()
    return t


def _recv_established(sock: socket.socket) -> bytes:
    sock.settimeout(5)
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            break
        resp += chunk
    return resp


def test_relay_works_when_client_waits_for_200(upstream, allow_local):
    """Control: the polite (wait-for-200) client relays both directions."""
    port, received, _ = upstream
    ours, theirs = socket.socketpair()
    _run_handle(theirs)
    ours.sendall(_connect_head(port))
    assert b"200 Connection Established" in _recv_established(ours)
    ours.sendall(b"EARLYLATE")
    ours.settimeout(5)
    assert ours.recv(4) == b"PONG"
    ours.close()


def test_pipelined_bytes_after_connect_header_reach_upstream(upstream, allow_local):
    """Bytes sent in the SAME segment as the CONNECT header must reach the
    upstream once the tunnel opens — dropping them kills every Bun/claude
    session while the log shows only 'allow' lines (2026-07-07 pilot)."""
    port, received, done = upstream
    ours, theirs = socket.socketpair()
    _run_handle(theirs)
    # header + optimistic first tunnel bytes in ONE segment
    ours.sendall(_connect_head(port) + b"EARLY")
    assert b"200 Connection Established" in _recv_established(ours)
    ours.sendall(b"LATE")
    ours.settimeout(5)
    assert ours.recv(4) == b"PONG"  # proves upstream saw LATE (and echoed)
    ours.close()
    done.wait(5)
    assert bytes(received) == b"EARLYLATE"  # EARLY not dropped, order preserved


# --- reverse plain-HTTP → verified-TLS listeners [RN-11] ---------------------
def _self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """An ephemeral self-signed cert for ``127.0.0.1`` (IP SAN) + its key, so an
    in-test TLS upstream can present a cert the reverse path verifies against."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2035, 1, 1, tzinfo=datetime.timezone.utc))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
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


class _RecordingUpstream:
    """A plain-TCP listener that only counts connections — for the paths that must
    NOT dial the upstream (preflight, invalid prefix)."""

    def __init__(self) -> None:
        self.connections = 0
        self.host = "127.0.0.1"
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self) -> None:
        self._srv.close()


class _TLSUpstream:
    """A TLS server on 127.0.0.1 running a per-test ``handler(ssl_conn)`` for each
    connection; counts connections so a test can assert exactly one dial."""

    def __init__(self, cert_file: Path, key_file: Path, handler) -> None:
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(str(cert_file), str(key_file))
        self._handler = handler
        self.connections = 0
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                raw, _ = self._srv.accept()
            except OSError:
                return
            self.connections += 1
            try:
                conn = self._ctx.wrap_socket(raw, server_side=True)
            except OSError:
                continue
            threading.Thread(target=self._handler, args=(conn,), daemon=True).start()

    def close(self) -> None:
        self._srv.close()


def _read_http_message(conn: socket.socket, timeout: float = 5) -> tuple[bytes, bytes]:
    """Read a full HTTP request (head + Content-Length body) from ``conn``."""
    conn.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return buf, b""
        buf += chunk
    head, body = buf.split(b"\r\n\r\n", 1)
    cl = 0
    for line in head.decode("latin1").split("\r\n")[1:]:
        if line.lower().startswith("content-length:"):
            cl = int(line.split(":", 1)[1].strip())
    while len(body) < cl:
        chunk = conn.recv(4096)
        if not chunk:
            break
        body += chunk
    return head, body


def _recv_all(sock: socket.socket, timeout: float = 5) -> bytes:
    sock.settimeout(timeout)
    buf = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except (TimeoutError, socket.timeout):
            break
        if not chunk:
            break
        buf += chunk
    return buf


def _trust_ctx(cert_file: Path):
    return lambda: ssl.create_default_context(cafile=str(cert_file))


def _log_lines(log_path: str) -> list[dict]:
    p = Path(log_path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _reverse_head(path: str, port: int, *, method: str = "POST", body_len: int = 0) -> bytes:
    lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "x-api-key: k",
    ]
    if body_len:
        lines.append(f"content-length: {body_len}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


@pytest.fixture()
def reverse_log(monkeypatch, tmp_path):
    log = str(tmp_path / "verdi.jsonl")
    monkeypatch.setattr(proxy, "LOG", log)
    return log


def test_reverse_preflight_head_is_answered_locally(reverse_log):
    """Bun sends ``HEAD /`` (and ``HEAD /t/<trial>``) before its first POST. Both
    get a 200, the connection closes, NO upstream dial happens, and — because the
    preflight never leaves the proxy — NO log line is written."""
    rec = _RecordingUpstream()
    try:
        for path in ("/", "/t/xyz"):
            ours, theirs = socket.socketpair()
            threading.Thread(
                target=proxy.handle_reverse, args=(theirs, rec.host, rec.port), daemon=True
            ).start()
            ours.sendall(f"HEAD {path} HTTP/1.1\r\nHost: h\r\n\r\n".encode())
            resp = _recv_all(ours)
            assert b"200 OK" in resp
            ours.close()
        assert rec.connections == 0
        assert _log_lines(reverse_log) == []
    finally:
        rec.close()


def test_reverse_missing_prefix_denies(reverse_log):
    """A base URL with no ``/t/<trial>`` prefix is a config error: 403 + one deny
    line attributed to trial ``-`` and the upstream host, with no dial."""
    rec = _RecordingUpstream()
    try:
        ours, theirs = socket.socketpair()
        threading.Thread(
            target=proxy.handle_reverse, args=(theirs, rec.host, rec.port), daemon=True
        ).start()
        ours.sendall(b"GET /v1/messages HTTP/1.1\r\nHost: h\r\n\r\n")
        resp = _recv_all(ours)
        assert b"403" in resp
        ours.close()
        assert _log_lines(reverse_log) == [
            {"trial": "-", "host": rec.host, "decision": "deny"}
        ]
        assert rec.connections == 0
    finally:
        rec.close()


def test_reverse_happy_path_strips_prefix_over_real_tls(reverse_log, monkeypatch, tmp_path):
    """Over REAL TLS: the ``/t/<trial>`` prefix is stripped (query preserved), Host
    is rewritten to the upstream, Connection becomes close, the api-key header rides
    through verbatim, the body (split across two sends, first chunk pipelined with
    the head) arrives intact and in order, and exactly one allow line is logged."""
    cert_file, key_file = _self_signed_cert(tmp_path)
    recorded: dict = {}

    def handler(conn):
        head, body = _read_http_message(conn)
        recorded["head"] = head
        recorded["body"] = body
        conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nOK")
        conn.close()

    up = _TLSUpstream(cert_file, key_file, handler)
    monkeypatch.setattr(proxy, "_upstream_context", _trust_ctx(cert_file))
    try:
        ours, theirs = socket.socketpair()
        threading.Thread(
            target=proxy.handle_reverse, args=(theirs, "127.0.0.1", up.port), daemon=True
        ).start()
        body = b'{"model":"claude","messages":[{"role":"user","content":"hi"}]}'
        head = _reverse_head("/t/t9/v1/messages?beta=true", up.port, body_len=len(body))
        first, second = body[:7], body[7:]
        ours.sendall(head + first)  # first body chunk pipelined with the head (Bun)
        ours.sendall(second)
        resp = _recv_all(ours)
        assert b"200 OK" in resp and resp.endswith(b"OK")
        ours.close()
    finally:
        up.close()

    rlines = recorded["head"].decode("latin1").split("\r\n")
    assert rlines[0] == "POST /v1/messages?beta=true HTTP/1.1"  # prefix stripped, query kept
    assert "Host: 127.0.0.1" in rlines  # rewritten to the upstream host (no :port)
    assert "Connection: close" in rlines
    assert "x-api-key: k" in rlines  # verbatim, in place
    assert recorded["body"] == body  # full body, order preserved
    assert _log_lines(reverse_log) == [
        {"trial": "t9", "host": "127.0.0.1", "decision": "allow"}
    ]


def test_reverse_streams_response_unbuffered(reverse_log, monkeypatch, tmp_path):
    """SSE-shaped: the upstream sends chunk A, waits until the test has OBSERVED the
    client already received A, then sends chunk B. A relay that buffers the whole
    response would deadlock here — the assertion is that A arrives before B is sent."""
    cert_file, key_file = _self_signed_cert(tmp_path)
    proceed = threading.Event()

    def handler(conn):
        _read_http_message(conn)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n"
        )
        conn.sendall(b"data: A\n\n")
        proceed.wait(5)  # only send B AFTER the test saw A on the client side
        conn.sendall(b"data: B\n\n")
        conn.close()

    up = _TLSUpstream(cert_file, key_file, handler)
    monkeypatch.setattr(proxy, "_upstream_context", _trust_ctx(cert_file))
    try:
        ours, theirs = socket.socketpair()
        threading.Thread(
            target=proxy.handle_reverse, args=(theirs, "127.0.0.1", up.port), daemon=True
        ).start()
        ours.sendall(_reverse_head("/t/tS/v1/messages", up.port))
        ours.settimeout(5)
        buf = b""
        while b"data: A" not in buf:
            chunk = ours.recv(4096)
            assert chunk, "connection closed before chunk A"
            buf += chunk
        assert b"data: B" not in buf  # B has not been sent yet (handler is blocked)
        proceed.set()
        while b"data: B" not in buf:
            chunk = ours.recv(4096)
            if not chunk:
                break
            buf += chunk
        assert b"data: A" in buf and b"data: B" in buf
        ours.close()
    finally:
        up.close()


def test_reverse_large_multirecord_response_is_complete(reverse_log, monkeypatch, tmp_path):
    """A 300KB response spans many TLS records; a missing ``pending()`` drain would
    stall the select loop and truncate it. The whole payload must arrive."""
    cert_file, key_file = _self_signed_cert(tmp_path)
    payload = bytes((i % 251 for i in range(300_000)))

    def handler(conn):
        _read_http_message(conn)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\ncontent-length: %d\r\nconnection: close\r\n\r\n" % len(payload)
        )
        conn.sendall(payload)
        conn.close()

    up = _TLSUpstream(cert_file, key_file, handler)
    monkeypatch.setattr(proxy, "_upstream_context", _trust_ctx(cert_file))
    try:
        ours, theirs = socket.socketpair()
        threading.Thread(
            target=proxy.handle_reverse, args=(theirs, "127.0.0.1", up.port), daemon=True
        ).start()
        ours.sendall(_reverse_head("/t/tL/v1/messages", up.port))
        resp = _recv_all(ours, timeout=10)
        ours.close()
    finally:
        up.close()
    head, _, got = resp.partition(b"\r\n\r\n")
    assert got == payload  # complete, byte-for-byte


def test_reverse_dial_refusal_is_502_and_deny(reverse_log):
    """When the upstream cannot be dialed (closed port), the trial gets a 502 and a
    deny line is attributed to it — never a silent hang."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()  # nothing listens here now
    ours, theirs = socket.socketpair()
    threading.Thread(
        target=proxy.handle_reverse, args=(theirs, "127.0.0.1", closed_port), daemon=True
    ).start()
    body = b"{}"
    ours.sendall(_reverse_head("/t/tD/v1/messages", closed_port, body_len=len(body)) + body)
    resp = _recv_all(ours)
    assert b"502" in resp
    ours.close()
    assert _log_lines(reverse_log) == [
        {"trial": "tD", "host": "127.0.0.1", "decision": "deny"}
    ]


def test_reverse_ports_parsing():
    """VERDI_REVERSE_PORTS parses multi-entry + host:port; a malformed entry raises
    (crashing startup loudly), never silently skipped."""
    assert proxy._parse_reverse_ports("") == {}
    assert proxy._parse_reverse_ports(
        "3129=api.anthropic.com,3130=api.openai.com:8443"
    ) == {3129: ("api.anthropic.com", 443), 3130: ("api.openai.com", 8443)}
    with pytest.raises(ValueError):
        proxy._parse_reverse_ports("3129")  # no '='
    with pytest.raises(ValueError):
        proxy._parse_reverse_ports("notaport=api.anthropic.com")  # non-numeric port
    with pytest.raises(ValueError):
        proxy._parse_reverse_ports("3129=")  # empty host
