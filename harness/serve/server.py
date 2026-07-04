"""Read-only HTTP observer [EVAL-13 AC-5, D002, D004].

stdlib ``ThreadingHTTPServer`` wrapping the three read seams — no new runtime
dependency for a loopback GET-only tool. The handler is the allowlist: four
routes, GET only; nothing here appends an event, writes a file, or triggers
execution. A UI-triggered mutation path is a different story with its own
actor plumbing and one-event obligations (spec: out of scope).

Route errors are *served*, not dropped: a failing read (corrupt heartbeat,
unreadable ledger) returns its message as a 500 JSON body — surfaced to the
observer while the server keeps answering other requests. An invalid tail
cursor (the ledger shrank — rewrite evidence) is 409, distinct from a merely
malformed offset (400).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..analyze.timeline import trial_timeline
from ..ledger.query import TailOffsetError, tail_events
from ..status.aggregate import compute_status
from .page import OPERATOR_PAGE

DEFAULT_HOST = "127.0.0.1"  # loopback by default: an operator tool, not a service
DEFAULT_PORT = 8383


class ObserverHandler(BaseHTTPRequestHandler):
    """GET-only routes over one experiment directory (bound by make_server)."""

    experiment_dir: Path  # bound per-server via make_server

    server_version = "verdi-bench-observer"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- routes ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send(200, OPERATOR_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/api/status":
                self._json(200, compute_status(self.experiment_dir))
            elif parsed.path == "/api/events":
                self._events(parsed.query)
            elif parsed.path == "/api/timeline":
                self._json(200, trial_timeline(self._ledger_path()))
            else:
                self._json(404, {"error": f"unknown path {parsed.path!r}"})
        except Exception as e:  # noqa: BLE001 — surfaced as a served 500, so one
            # failing read cannot take the observer down for other requests; the
            # message travels to the client instead of vanishing into a dropped
            # connection. Nothing is retried or defaulted.
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _events(self, query: str) -> None:
        raw = parse_qs(query).get("offset", ["0"])[0]
        try:
            offset = int(raw)
        except ValueError:
            self._json(400, {"error": f"offset must be an integer, got {raw!r}"})
            return
        try:
            events, next_offset = tail_events(self._ledger_path(), offset)
        except TailOffsetError as e:
            self._json(409, {"error": str(e)})  # cursor invalid: rewrite evidence
            return
        self._json(200, {"events": events, "next_offset": next_offset})

    def _ledger_path(self) -> Path:
        return Path(self.experiment_dir) / "ledger.ndjson"

    # -- non-GET methods are refused, naming the one allowed method ------------
    def _method_not_allowed(self) -> None:
        body = json.dumps({"error": "read-only observer: GET only"}).encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _method_not_allowed  # noqa: N815 - BaseHTTPRequestHandler contract
    do_PUT = _method_not_allowed  # noqa: N815
    do_DELETE = _method_not_allowed  # noqa: N815
    do_PATCH = _method_not_allowed  # noqa: N815

    # -- plumbing ---------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # live data, never cached
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(
            status,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            "application/json",
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
        """Quiet per-request stderr noise: the CLI prints the one line that
        matters (where the observer is), and every request is a read."""


def make_server(
    experiment_dir, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    """Bind a threaded observer server to one experiment directory.

    ``port=0`` asks the OS for an ephemeral port (tests); the caller reads the
    realized address from ``server_address``. The caller owns the lifecycle
    (``serve_forever`` / ``shutdown`` / ``server_close``).
    """
    handler = type(
        "BoundObserverHandler",
        (ObserverHandler,),
        {"experiment_dir": Path(experiment_dir)},
    )
    return ThreadingHTTPServer((host, port), handler)
