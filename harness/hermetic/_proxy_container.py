"""Minimal verdi metering CONNECT proxy (stdlib only) [refactor 04 §1].

The single maintained copy of the metering proxy (promoted from
``scripts/shakedown/assets/harbor/proxy.py``, which Phase 3D deletes). It runs
**inside** the pinned base image — mounted read-only, never imported by the
harness — so it must stay stdlib-only.

Fixing the old three-way manual sync between ``run.config.yaml``, the proxy's
hardcoded set, and ``squid.conf``: the allowlist is **injected** from
``VERDI_PROXY_ALLOW`` (comma-separated hosts) rather than hardcoded.

The wire contract is **frozen** (external deployments + ``test_e2e_metering_proxy``
pin it): the trial id arrives as the basic-auth USERNAME with an empty password,
and every request appends one JSONL line in the exact shape
``harness/run/engines/harbor.py:_scan_proxy_log`` parses:
    {"trial": <username>, "host": <host>, "decision": "allow"|"deny"}
so egress is attributed per trial and any denied host is an egress violation.

Reverse plain-HTTP → verified-TLS listeners [RN-11]. The pinned ``claude`` CLI
(native bun binary) ignores HTTP(S)_PROXY and /etc/hosts entirely
(anthropics/claude-code#14165), so on the internal metered network its streaming
client cannot egress through the frozen CONNECT tunnel — the 2026-07-07 pilot's
bare arms each burned their budget into ConnectionRefused while the metering log
showed only healthy "allow" lines. It DOES honor ``ANTHROPIC_BASE_URL`` over plain
HTTP, so harbor points that base URL (carrying a ``/t/<trial-id>`` prefix) at a
reverse listener here: the listener terminates the trial's plain HTTP, attributes
the trial from the path prefix, originates a VERIFIED TLS connection to the real
upstream, and splices bytes both ways. This is **additive** to the frozen CONNECT
contract — same ``log()`` JSONL shape (one allow/deny line per HTTP request), same
allowlist injection (a reverse port exists only for an allowlisted host). External
squid-based deployments simply do not set ``VERDI_REVERSE_PORTS``.
"""
from __future__ import annotations

import base64
import json
import os
import select
import socket
import ssl
import threading

# INJECTED (was a hardcoded set): the resolved allowlist arrives as env so the
# proxy, run.config.yaml, and the engine's ProxyConfig cannot drift [refactor 04 §1].
ALLOW = frozenset(h for h in os.environ.get("VERDI_PROXY_ALLOW", "").split(",") if h)
LOG = os.environ.get("PROXY_LOG", "/var/log/verdi/verdi.jsonl")
PROXY_PORT = 3128
_lock = threading.Lock()


def _parse_reverse_ports(spec):
    """Parse ``VERDI_REVERSE_PORTS`` (``"<port>=<host[:upstream_port]>,..."``) into
    ``{port: (host, upstream_port)}`` — upstream port defaults to 443 [RN-11]. A
    malformed entry RAISES (crashing startup loudly so readiness fails with the
    container logs visible), never silently skipped."""
    out = {}
    for entry in (e.strip() for e in spec.split(",") if e.strip()):
        port_s, sep, hostspec = entry.partition("=")
        host, _, up_s = hostspec.partition(":")
        if not sep or not port_s.isdigit() or not host or (up_s and not up_s.isdigit()):
            raise ValueError(
                f"malformed VERDI_REVERSE_PORTS entry {entry!r}; "
                "expected <port>=<host[:upstream_port]>"
            )
        out[int(port_s)] = (host, int(up_s) if up_s else 443)
    return out


# INJECTED like ALLOW: which reverse listeners to bind, and which upstream each
# fronts. Parsed at import so a malformed value crashes before main() binds.
REVERSE_PORTS = _parse_reverse_ports(os.environ.get("VERDI_REVERSE_PORTS", ""))


def log(trial, host, decision):
    with _lock, open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"trial": trial, "host": host, "decision": decision}) + "\n")
        f.flush()


def _trial_from(headers):
    auth = headers.get("proxy-authorization", "").strip()
    if auth.lower().startswith("basic "):
        try:
            up = base64.b64decode(auth.split(" ", 1)[1]).decode("latin1")
            return up.split(":", 1)[0] or "-"
        except Exception:
            return "-"
    return "-"


def handle(client):
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = client.recv(4096)
            if not chunk:
                return
            req += chunk
        raw_head, early = req.split(b"\r\n\r\n", 1)
        head = raw_head.decode("latin1")
        lines = head.split("\r\n")
        method, target = lines[0].split(" ")[:2]
        headers = {k.strip().lower(): v.strip() for k, v in
                   (l.split(":", 1) for l in lines[1:] if ":" in l)}
        trial = _trial_from(headers)
        if method != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n"); return
        host, _, port = target.partition(":")
        port = int(port or 443)
        if host not in ALLOW:
            log(trial, host, "deny")
            client.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n"); return
        if trial == "-":
            log(trial, host, "deny")
            client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                           b"Proxy-Authenticate: Basic realm=\"verdi\"\r\n\r\n"); return
        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except Exception:
            log(trial, host, "deny")
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n"); return
        log(trial, host, "allow")
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        # Forward any bytes the client pipelined behind the CONNECT header
        # (Bun/claude sends the TLS ClientHello optimistically in the same
        # segment); dropping them leaves the tunnel permanently dead while the
        # log shows a healthy "allow" (2026-07-07 pilot failure).
        if early:
            upstream.sendall(early)
        socks = [client, upstream]
        while True:
            r, _, _ = select.select(socks, [], [], 120)
            if not r:
                break
            stop = False
            for s in r:
                data = s.recv(65536)
                if not data:
                    stop = True; break
                (upstream if s is client else client).sendall(data)
            if stop:
                break
        upstream.close()
    except Exception:
        pass
    finally:
        client.close()


def _upstream_context():
    """The TLS context the reverse path originates upstream connections with — a
    module-level seam so tests can substitute a context trusting a test CA.
    ``create_default_context`` honors SSL_CERT_FILE, which the docker e2e sets."""
    return ssl.create_default_context()


def _is_preflight_path(path):
    """A Bun preflight target is ``/`` or exactly ``/t/<something>`` (no further
    segment) — the base-URL probe that never reaches the upstream [RN-11]."""
    if path == "/":
        return True
    if path.startswith("/t/"):
        rest = path[len("/t/"):]
        return rest != "" and "/" not in rest
    return False


def _parse_trial_path(path):
    """``/t/<trial>/<rest...>`` → ``(trial, rest)``; anything else → ``(None, None)``.
    The trial (first segment after ``/t/``) is how the reverse path attributes egress
    (the CONNECT path uses proxy-auth userinfo instead) [RN-11]."""
    if not path.startswith("/t/"):
        return None, None
    trial, sep, rest = path[len("/t/"):].partition("/")
    if not trial or not sep:
        return None, None
    return trial, rest


def _splice_reverse(client, upstream):
    """Bidirectional relay for the reverse path, where ``upstream`` is an
    ``ssl.SSLSocket``. ``select`` watches the raw fd, but decrypted bytes can sit in
    the SSL layer's buffer with nothing new on the fd — so after each upstream recv
    we DRAIN ``pending()`` before returning to ``select``; a naive select loop stalls
    mid-response. Bytes pass through unbuffered, so SSE streaming works [RN-11]."""
    socks = [client, upstream]
    while True:
        r, _, _ = select.select(socks, [], [], 120)
        if not r:
            break
        stop = False
        for s in r:
            data = s.recv(65536)
            if not data:
                stop = True
                break
            if s is upstream:
                client.sendall(data)
                while upstream.pending():
                    chunk = upstream.recv(65536)
                    if not chunk:
                        stop = True
                        break
                    client.sendall(chunk)
                if stop:
                    break
            else:
                upstream.sendall(data)
        if stop:
            break


def handle_reverse(client, upstream_host, upstream_port=443):
    """Terminate a trial's plain HTTP, attribute it from the ``/t/<trial>`` path
    prefix, originate verified TLS to ``upstream_host``, and splice both ways [RN-11].

    Additive to the frozen CONNECT contract: a proxy-defiant client (the pinned
    claude CLI ignores HTTP(S)_PROXY — claude-code#14165) is steered here by
    ``ANTHROPIC_BASE_URL`` and still metered per trial (one allow/deny line/request).
    """
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = client.recv(4096)
            if not chunk:
                return
            req += chunk
        # keep `early`: the body's first bytes often ride in the same segment as
        # the head (the Bun pipelining that the CONNECT path also handles).
        raw_head, early = req.split(b"\r\n\r\n", 1)
        lines = raw_head.decode("latin1").split("\r\n")
        method, target = lines[0].split(" ")[:2]
        path, sep_q, query = target.partition("?")
        # Preflight: Bun sends HEAD / (or HEAD /t/<trial>) before the first POST. It
        # never leaves the proxy, so answer locally and do NOT log — a log line here
        # would pollute per-trial egress attribution.
        if method == "HEAD" and _is_preflight_path(path):
            client.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
            return
        trial, rest = _parse_trial_path(path)
        if trial is None:
            log("-", upstream_host, "deny")
            body = (
                b"verdi metering reverse proxy: the base URL must carry a "
                b"/t/<trial-id> prefix so egress is attributable\n"
            )
            client.sendall(
                b"HTTP/1.1 403 Forbidden\r\ncontent-type: text/plain; charset=utf-8\r\n"
                b"content-length: " + str(len(body)).encode() + b"\r\n"
                b"connection: close\r\n\r\n" + body
            )
            return
        # Rewrite the head: our request line (prefix stripped, query preserved) plus
        # the Host/Connection we control; drop hop-by-hop proxy-* headers; EVERY other
        # header rides through byte-verbatim in original order — Content-Length /
        # Transfer-Encoding / Accept-Encoding untouched, so the body and its framing
        # pass through unchanged.
        new_target = "/" + rest + (("?" + query) if sep_q else "")
        kept = []
        for l in lines[1:]:
            name = l.split(":", 1)[0].strip().lower() if ":" in l else ""
            if name in ("host", "connection") or name.startswith("proxy-"):
                continue
            kept.append(l)
        new_head = "\r\n".join(
            [f"{method} {new_target} HTTP/1.1", f"Host: {upstream_host}", "Connection: close"]
            + kept
        ).encode("latin1") + b"\r\n\r\n"
        try:
            raw = socket.create_connection((upstream_host, upstream_port), timeout=30)
            upstream = _upstream_context().wrap_socket(raw, server_hostname=upstream_host)
        except Exception:
            log(trial, upstream_host, "deny")
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        log(trial, upstream_host, "allow")
        try:
            upstream.sendall(new_head + early)
            _splice_reverse(client, upstream)
        finally:
            upstream.close()
    except Exception:
        pass
    finally:
        client.close()


def _listen(port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(128)
    return srv


def _accept_loop(srv, handler):
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handler, args=(c,), daemon=True).start()


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    # Bind + listen EVERY reverse port BEFORE the CONNECT port: the managed sidecar's
    # readiness probe only checks 3128, so binding 3128 last makes "3128 accepts"
    # imply every reverse listener is already accepting too [RN-11].
    reverse = [
        (_listen(port), host, up_port) for port, (host, up_port) in REVERSE_PORTS.items()
    ]
    srv = _listen(PROXY_PORT)
    print("verdi mini metering proxy on :3128", flush=True)
    for rsrv, host, up_port in reverse:
        threading.Thread(
            target=_accept_loop,
            args=(rsrv, lambda c, h=host, p=up_port: handle_reverse(c, h, p)),
            daemon=True,
        ).start()
    _accept_loop(srv, handle)


if __name__ == "__main__":
    main()
