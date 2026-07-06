"""Tier-neutral web kit for the loopback surfaces [refactor 07 §4].

The operator observer (``harness.serve``), the blinded reviewer surface
(``harness.review``), and the authoring ceremony (``harness.author``) each
stood up their own ``BaseHTTPRequestHandler`` and triplicated ~90 lines of the
same JSON-over-loopback plumbing — ``_send``/``_json``/``_body``, the quiet
``log_message``, the ``type("Bound…Handler", …)`` state-injection factory — plus
the mechanical page chunks (the design-token CSS, the ``h()`` DOM builder and
``j()`` fetch wrapper). They were copy-maintained rather than shared because the
reviewer-surface-isolation contract (``.importlinter``, strict + transitive)
forbids ``harness.review`` from importing ``harness.serve``/``status``/``author``.

**Placement is the whole design.** This package imports *none* of those four
surfaces (only the equally-neutral ``harness.http_guard``), exactly as
``http_guard`` itself proved the neutral-package pattern. A surface importing
``harness.webkit`` therefore cannot reach a peer surface through it, so the
isolation property survives the dedup instead of becoming a discipline.

- :mod:`harness.webkit.http` — Layer 1: :class:`JsonRouteHandler` (route-table
  dispatch, the ``RouteError`` → status mapping, host/CSRF guard invocation
  composed from ``http_guard``) and :func:`bind_handler`.
- :mod:`harness.webkit.page` — Layer 2: the shared mechanical page chunks
  (design-token CSS, the ``h()``/``j()`` kit) composed into each self-contained
  document at import time. Banners and product wording stay per-surface.
"""

from __future__ import annotations

from .http import (
    ChainBroken,
    JsonRouteHandler,
    NotFound,
    Refused,
    RouteError,
    bind_handler,
    default_error,
)

__all__ = [
    "JsonRouteHandler",
    "RouteError",
    "NotFound",
    "Refused",
    "ChainBroken",
    "bind_handler",
    "default_error",
]
