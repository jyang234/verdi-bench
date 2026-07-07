"""Minimal verdi OTLP/HTTP trace collector (stdlib only) [refactor 09 §2].

The sidecar the trial container POSTs its spans to. A sibling of
``_proxy_container.py``: mounted read-only into the pinned ``python:3.12-alpine``
base, run in place (``python3 /verdi/collector.py``), **never imported by the
harness at runtime** — so, like the proxy, it must stay stdlib-only.

It is a **dumb receiver** [refactor 09 §1]: it never parses span payloads and
never interprets OTLP. Each accepted ``POST /v1/traces`` appends exactly one
envelope line to a JSONL log and returns 200; decoding happens harness-side,
post-trial, deterministically (``harness/hermetic/otlp_decode.py``). Raw bytes
are the evidence; interpretation is replayable.

Envelope contract [refactor 09 §2] — one line per accepted request::

    {"trial": "<id>", "seq": 41, "content_type": "application/x-protobuf", "body_b64": "..."}

* ``seq`` is a process-lifetime counter — **no wall-clock timestamps** in the
  envelope (determinism; spans carry their own times).
* JSON bodies are embedded as ``body_json`` (parsed, inline); protobuf as
  ``body_b64`` (base64 of the raw bytes). ``Content-Encoding: gzip`` is honored
  before either.
* Trial attribution rides the ``x-verdi-trial`` request header — the collector's
  analog of the proxy's trial-id-as-userinfo. A request without it is recorded
  with ``"trial": "-"`` and answered **400** — logged, excluded from every
  trial's extraction, countable by operators. Fail loud, lose nothing.

Log path from ``COLLECTOR_LOG`` (default ``/var/log/verdi/otlp.jsonl``) — the
host-side lifecycle passes the *basename* through this env and mounts the parent
dir, so a custom log path can never silently fall open (the ``988af58`` proxy
lesson, applied from day one).
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import socket
import threading

# The OTLP/HTTP standard port; the host lifecycle addresses the collector here.
COLLECTOR_PORT = int(os.environ.get("COLLECTOR_PORT", "4318"))
# INJECTED basename-under-mounted-dir (the PROXY_LOG discipline, commit 988af58):
# the host passes the operator's basename and mounts the parent, so a custom log
# path is honored exactly rather than falling open beside a touched-but-empty file.
LOG = os.environ.get("COLLECTOR_LOG", "/var/log/verdi/otlp.jsonl")

# The only accepted request target. Everything else (incl. /v1/logs, /v1/metrics)
# is 404 in v1 [refactor 09 §2].
_TRACES_PATH = "/v1/traces"
_JSON = "application/json"
_PROTOBUF = "application/x-protobuf"

_log_lock = threading.Lock()
_seq_lock = threading.Lock()
_seq = 0


def _next_seq() -> int:
    """A process-lifetime counter — the envelope's ONLY ordering key, so the log
    carries no wall-clock and a replayed request sequence is byte-identical."""
    global _seq
    with _seq_lock:
        n = _seq
        _seq += 1
        return n


def _append(envelope: dict) -> None:
    with _log_lock, open(LOG, "a", encoding="utf-8") as f:
        # Insertion order (not sorted): the envelope carries no timestamp, so the
        # bytes are already deterministic for a given (seq, body) sequence.
        f.write(json.dumps(envelope) + "\n")
        f.flush()


def _read_headers(client) -> tuple[dict, bytes]:
    """Read up to the header terminator; return (headers, leftover-body-bytes)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = client.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    request_line = lines[0].decode("latin1") if lines else ""
    headers = {
        k.strip().lower(): v.strip()
        for k, v in (
            ln.decode("latin1").split(":", 1) for ln in lines[1:] if b":" in ln
        )
    }
    headers["__request_line__"] = request_line
    return headers, rest


def _read_body(client, headers: dict, leftover: bytes) -> bytes:
    """Read exactly ``Content-Length`` bytes (accounting for what already arrived
    with the header block). OTLP/HTTP exporters always send Content-Length."""
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        length = 0
    body = leftover
    while len(body) < length:
        chunk = client.recv(65536)
        if not chunk:
            break
        body += chunk
    return body[:length] if length else body


def handle(client) -> None:
    try:
        headers, leftover = _read_headers(client)
        parts = headers.pop("__request_line__", "").split(" ")
        method = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        path = target.split("?", 1)[0]
        if method != "POST" or path != _TRACES_PATH:
            # Everything but POST /v1/traces (incl. /v1/logs, /v1/metrics) → 404.
            client.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            return
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in (_JSON, _PROTOBUF):
            client.sendall(
                b"HTTP/1.1 415 Unsupported Media Type\r\nContent-Length: 0\r\n\r\n"
            )
            return
        body = _read_body(client, headers, leftover)
        if headers.get("content-encoding", "").lower() == "gzip":
            body = gzip.decompress(body)

        # Trial attribution: the standard OTEL_EXPORTER_OTLP_HEADERS mechanism the
        # engine injects. Absent → recorded as "-" and answered 400 (fail loud).
        trial = headers.get("x-verdi-trial", "").strip() or "-"
        envelope: dict = {"trial": trial, "seq": _next_seq(), "content_type": content_type}
        if content_type == _JSON:
            try:
                envelope["body_json"] = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Malformed JSON transport (not a span-semantics judgement): reject
                # loudly without recording an unusable envelope.
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                return
        else:
            envelope["body_b64"] = base64.b64encode(body).decode("ascii")
        _append(envelope)

        if trial == "-":
            # Recorded (operators can count it) but refused — an unattributed span
            # post attaches to no trial's extraction [refactor 09 §2].
            msg = b"missing x-verdi-trial header"
            client.sendall(
                b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n"
                b"Content-Length: " + str(len(msg)).encode("ascii") + b"\r\n\r\n" + msg
            )
            return
        # 200 with an empty body: a zero-length ExportTraceServiceResponse is valid,
        # and every OTLP/HTTP exporter treats a 2xx as success.
        client.sendall(
            f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: 0\r\n\r\n".encode(
                "latin1"
            )
        )
    except Exception:
        # A dumb receiver never crashes the connection loop over one bad request.
        pass
    finally:
        client.close()


def _serve(srv: socket.socket) -> None:
    """Accept loop, thread-per-connection (the proxy's shape). Split from
    :func:`main` so a test can drive it on an ephemeral socket."""
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def main() -> None:
    # Pre-touch the log so a zero-span trial still finds a present (empty) log
    # rather than a missing one [refactor 09 §2, the metering.py pre-touch pattern].
    os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
    open(LOG, "a", encoding="utf-8").close()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", COLLECTOR_PORT))
    srv.listen(128)
    print(f"verdi otlp collector on :{COLLECTOR_PORT}", flush=True)
    _serve(srv)


if __name__ == "__main__":
    main()
