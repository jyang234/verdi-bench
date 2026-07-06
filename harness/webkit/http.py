"""Layer 1 — the shared JSON-over-loopback handler [refactor 07 §4].

One :class:`JsonRouteHandler` base carries the plumbing the three surfaces
triplicated: ``_send``/``_json``/``_read_json_object``, the quiet
``log_message``, host/CSRF guard invocation (composed from
``harness.http_guard`` — never forked), route-*table* dispatch (replacing the
per-surface ``if/elif`` chains), the ``RouteError`` → HTTP-status mapping, and
the 405 method-not-allowed sender. :func:`bind_handler` replaces the three
``type("Bound…Handler", (Handler,), {...})`` state-injection factories.

Each surface keeps its own route table, its own guard choice (GET-only host
check vs. the mutation surfaces' host+CSRF pair), its own error map, and its own
banner text — this module owns the mechanical transport, never the semantics.
The ``RouteError`` subclasses give the *common* status mapping (404/409); a
surface whose refusal payload differs (the reviewer/author POST endpoints add an
``error_class`` field; the author preview reads keep their own ``/api`` quirks)
supplies its own error map, so no per-surface behavior is flattened.

Imports nothing from ``serve``/``status``/``author``/``review`` — placement is
the design (see the package docstring), so importing this from the reviewer
surface cannot reach the operator tier.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Callable, Optional
from urllib.parse import urlparse

from ..http_guard import ForbiddenError, check_csrf, check_host


class RouteError(Exception):
    """A route-level failure that maps to an HTTP status served as JSON.

    Subclasses set :attr:`status`; the message becomes the response's ``error``
    field. The base is deliberately never raised directly — surfaces raise the
    typed subclasses (or their own subclasses of them) so the status is a
    property of the exception, not of a hand-written ``except`` ladder.
    """

    status: int = 500


class NotFound(RouteError):
    """404 — the route or resource does not exist on this surface."""

    status = 404


class Refused(RouteError):
    """409 — the operation is refused for a stated reason (conflict/precondition)."""

    status = 409


class ChainBroken(RouteError):
    """409 — a ledger-reading route withholds tampered content [PRA-M10]."""

    status = 409


def default_error(exc: BaseException) -> Optional[tuple[int, dict]]:
    """The common exception→response map: a :class:`RouteError` serves its own
    status and message; anything else returns ``None`` so the dispatcher falls
    through to a served 500 (never a dropped connection)."""
    if isinstance(exc, RouteError):
        return (exc.status, {"error": str(exc)})
    return None


class JsonRouteHandler(BaseHTTPRequestHandler):
    """Shared JSON-over-loopback request handler [refactor 07 §4].

    A surface subclasses this, binds its per-server state with
    :func:`bind_handler`, and implements ``do_GET``/``do_POST`` by building a
    ``{path: handler}`` table and calling :meth:`dispatch`.
    """

    # 405 for unknown verbs (not stdlib's 501), no ``Server``/``Python`` banner.
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- response plumbing (was triplicated verbatim across the three surfaces) --
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # live data, never cached
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(
            status,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            "application/json",
        )

    def _read_json_object(self) -> dict:
        """Parse the request body as a JSON object, or refuse with a 404-mapping
        :class:`NotFound` (the mutation surfaces' original ``_body`` behavior)."""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise NotFound(f"request body is not JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise NotFound("request body must be a JSON object")
        return parsed

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
        """Quiet per-request stderr: the CLI prints the one line that matters
        (where the surface is), and these are loopback tools, not services."""

    # -- guards: compose http_guard, never fork it [PRA-H2, PRA-M16] --
    def _guard_host(self) -> None:
        check_host(self.headers, self.server.server_address)

    def _guard_host_and_csrf(self) -> None:
        check_host(self.headers, self.server.server_address)
        check_csrf(self.headers, self.server.server_address)

    # -- dispatch: route-TABLE + the try/guard/except scaffolding, once --
    def dispatch(
        self,
        table: dict[str, Callable[[], None]],
        *,
        guard: Callable[[], None],
        unknown: Callable[[str], tuple[int, dict]],
        error: Callable[[BaseException], Optional[tuple[int, dict]]] = default_error,
    ) -> None:
        """Run ``guard``, route the request path through ``table``, and serve
        errors as JSON — the shared shape of every ``do_GET``/``do_POST``.

        ``table`` maps a path to a zero-arg handler that sends its own response;
        an unmatched path is served via ``unknown(path)``. ``ForbiddenError``
        (from the guard) is always a 403. Any other exception is offered to
        ``error(exc)``: a ``(status, payload)`` is served, ``None`` falls through
        to a served 500 — so one failing read cannot drop the connection or take
        the surface down for the next request. Nothing is retried or defaulted.
        """
        path = urlparse(self.path).path
        try:
            guard()
            handler = table.get(path)
            if handler is None:
                self._json(*unknown(path))
            else:
                handler()
        except ForbiddenError as e:
            self._json(403, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — served as JSON, never dropped
            resp = error(e)
            self._json(*(resp if resp is not None else
                         (500, {"error": f"{type(e).__name__}: {e}"})))

    def _method_not_allowed(self, allow: str, message: str) -> None:
        """Send a 405 naming the allowed method(s) — the shared body of every
        surface's ``do_PUT``/``do_DELETE``/… aliases (405 + ``Allow``, not
        stdlib's 501 [PRA-L10]). The ``allow`` string and ``message`` are the
        per-surface content."""
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", allow)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def bind_handler(base: type, name: str, /, **attrs) -> type:
    """Bind per-server state onto a fresh handler subclass [refactor 07 §4].

    Replaces the three ``type("Bound…Handler", (Handler,), {...})`` factories
    (and the author server's dynamic-subclass state injection): the bound
    directories, actor, corpus manifest, lock kwargs, … become class attributes
    the request handler reads, exactly as ``ThreadingHTTPServer`` requires a
    handler *type*, not an instance.
    """
    return type(name, (base,), attrs)
