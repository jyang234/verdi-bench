"""The shared JSON-over-loopback handler behaves like the surfaces it replaces
[refactor 07 §4].

Drives a tiny :class:`JsonRouteHandler` subclass over real loopback HTTP (no
browser, no surface import) and pins the mechanical behavior the three surfaces
depended on: route-table dispatch, the ``RouteError`` → status mapping, the
served-500 fallback (a failing route never drops the connection), the JSON
plumbing (sorted keys, ``Cache-Control: no-store``), the host/CSRF guards
(composed from ``http_guard``), the 405 method-not-allowed sender, and
:func:`bind_handler` state injection.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from harness.webkit import http as webkit_http
from harness.webkit.http import (
    ChainBroken,
    JsonRouteHandler,
    NotFound,
    Refused,
    RouteError,
    bind_handler,
    default_error,
)
from tests.fixtures.servers import running_server


class _Boom(Exception):
    """An un-mapped error: must surface as a served 500, never a drop."""


class _Handler(JsonRouteHandler):
    """A minimal GET+POST surface exercising every dispatch path."""

    server_version = "test-kit"
    tag = None  # bound per-server by bind_handler

    def do_GET(self):  # noqa: N802
        table = {
            "/": lambda: self._send(200, b"<!doctype html>ok", "text/html; charset=utf-8"),
            "/api/echo": lambda: self._json(200, {"tag": self.tag, "z": 1, "a": 2}),
            "/api/nf": lambda: (_ for _ in ()).throw(NotFound("missing")),
            "/api/refuse": lambda: (_ for _ in ()).throw(Refused("nope")),
            "/api/broken": lambda: (_ for _ in ()).throw(ChainBroken("tampered")),
            "/api/boom": lambda: (_ for _ in ()).throw(_Boom("kaboom")),
        }
        self.dispatch(
            table,
            guard=self._guard_host,
            unknown=lambda p: (404, {"error": f"unknown path {p!r}"}),
            error=default_error,
        )

    def do_POST(self):  # noqa: N802
        def _create():
            body = self._read_json_object()
            self._json(200, {"got": sorted(body)})

        self.dispatch(
            {"/api/create": _create},
            guard=self._guard_host_and_csrf,
            unknown=lambda p: (404, {"error": f"no endpoint {p!r}"}),
            error=default_error,
        )

    def _refuse(self):
        self._method_not_allowed("GET, POST", "test surface: GET and POST only")

    do_PUT = _refuse  # noqa: N815
    do_DELETE = _refuse  # noqa: N815


def _server(port=0):
    return ThreadingHTTPServer(("127.0.0.1", port), bind_handler(_Handler, "BoundTest", tag="bound!"))


def _get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return resp.status, dict(resp.headers), resp.read()


def test_route_table_dispatch_and_bound_state():
    with running_server(_server()) as base:
        status, headers, body = _get(base, "/api/echo")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store"
    # _json sorts keys deterministically (default separators); bind_handler
    # injected the per-server state
    assert body == b'{"a": 2, "tag": "bound!", "z": 1}'


def test_page_route_and_unknown_path():
    with running_server(_server()) as base:
        status, headers, body = _get(base, "/")
        assert status == 200 and headers["Content-Type"].startswith("text/html")
        assert body == b"<!doctype html>ok"
        with pytest.raises(urllib.error.HTTPError) as ei:
            _get(base, "/api/does-not-exist")
    assert ei.value.code == 404
    assert json.loads(ei.value.read())["error"] == "unknown path '/api/does-not-exist'"


@pytest.mark.parametrize(
    "path, code",
    [("/api/nf", 404), ("/api/refuse", 409), ("/api/broken", 409), ("/api/boom", 500)],
)
def test_route_error_status_mapping_and_served_500(path, code):
    with running_server(_server()) as base:
        with pytest.raises(urllib.error.HTTPError) as ei:
            _get(base, path)
    assert ei.value.code == code
    payload = json.loads(ei.value.read())
    # an un-mapped error is SERVED (500 with a typed message), never dropped
    if code == 500:
        assert payload["error"] == "_Boom: kaboom"
    else:
        assert payload["error"] in ("missing", "nope", "tampered")


def test_host_guard_rejects_foreign_host_with_403():
    with running_server(_server()) as base:
        with pytest.raises(urllib.error.HTTPError) as ei:
            _get(base, "/api/echo", headers={"Host": "evil.example"})
    assert ei.value.code == 403


def test_post_requires_csrf_origin_then_reads_json_object():
    with running_server(_server()) as base:
        # no Origin: the CSRF guard refuses a state-changing request with 403
        req = urllib.request.Request(
            base + "/api/create", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req)
        assert ei.value.code == 403
        # same-origin JSON POST passes the guard and parses the body
        ok = urllib.request.Request(
            base + "/api/create", data=b'{"b": 1, "a": 2}', method="POST",
            headers={"Content-Type": "application/json", "Origin": base},
        )
        with urllib.request.urlopen(ok) as resp:
            assert json.loads(resp.read()) == {"got": ["a", "b"]}
        # a non-JSON body is refused as 404 (the surfaces' _body contract)
        bad = urllib.request.Request(
            base + "/api/create", data=b"not json", method="POST",
            headers={"Content-Type": "application/json", "Origin": base},
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(bad)
    assert ei.value.code == 404


def test_method_not_allowed_names_the_allowed_verbs():
    with running_server(_server()) as base:
        req = urllib.request.Request(base + "/", method="DELETE")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req)
    assert ei.value.code == 405
    assert ei.value.headers["Allow"] == "GET, POST"


def test_default_error_maps_only_route_errors():
    assert default_error(NotFound("x")) == (404, {"error": "x"})
    assert default_error(Refused("y")) == (409, {"error": "y"})
    assert default_error(ChainBroken("z")) == (409, {"error": "z"})
    assert default_error(ValueError("v")) is None
    # the base is a 500-mapping RouteError, but surfaces raise the typed kinds
    assert RouteError().status == 500
    assert webkit_http.NotFound.status == 404
