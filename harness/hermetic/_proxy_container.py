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
"""
from __future__ import annotations

import base64
import json
import os
import select
import socket
import threading

# INJECTED (was a hardcoded set): the resolved allowlist arrives as env so the
# proxy, run.config.yaml, and the engine's ProxyConfig cannot drift [refactor 04 §1].
ALLOW = frozenset(h for h in os.environ.get("VERDI_PROXY_ALLOW", "").split(",") if h)
LOG = os.environ.get("PROXY_LOG", "/var/log/verdi/verdi.jsonl")
_lock = threading.Lock()


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
        head = req.split(b"\r\n\r\n", 1)[0].decode("latin1")
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


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 3128))
    srv.listen(128)
    print("verdi mini metering proxy on :3128", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
