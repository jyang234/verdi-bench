"""Shared loopback web-surface guard [PRA-H2, PRA-M16].

The author ceremony, the reviewer capture surface, and the operator observer all
bind to loopback. Loopback binding stops a remote TCP peer but NOT a page the
operator's own browser loads: that page can issue a cross-site ``POST`` to
``127.0.0.1`` (CSRF — e.g. forging an ``experiment_locked`` genesis event) or,
via DNS rebinding, read the loopback response (the unblinded operator data whose
viewers are disqualified as blinded reviewers).

Two checks close both holes:

* :func:`check_host` — the ``Host`` header must name the bound loopback origin,
  defeating DNS rebinding (an attacker page resolves its own hostname to
  127.0.0.1 but still sends its own ``Host``).
* :func:`check_csrf` — a state-changing request must carry a same-origin
  ``Origin`` and ``Content-Type: application/json``, rejecting both the
  cross-site form/fetch and the ``text/plain`` no-cors bypass.

This module imports nothing from the surfaces it guards, so the
reviewer-surface-isolation import contract is unaffected.
"""

from __future__ import annotations


class ForbiddenError(Exception):
    """403: the request's Host/Origin/Content-Type is not allowed."""


def _loopback_names(bound_host: str) -> set[str]:
    return {bound_host, "127.0.0.1", "localhost", "::1", "[::1]"}


def check_host(headers, server_address) -> None:
    """Reject a ``Host`` header that is not the bound loopback origin [PRA-M16]."""
    bound_host, port = server_address[0], server_address[1]
    names = _loopback_names(bound_host)
    allowed = {f"{n}:{port}" for n in names} | names  # port optional in Host
    host = headers.get("Host", "")
    if host not in allowed:
        raise ForbiddenError(
            f"Host {host!r} not allowed; this surface is loopback-only [PRA-M16]"
        )


def check_csrf(headers, server_address) -> None:
    """Reject a state-changing request that is cross-origin, missing its
    ``Origin``, or not ``application/json`` [PRA-H2]."""
    bound_host, port = server_address[0], server_address[1]
    allowed = {f"http://{n}:{port}" for n in _loopback_names(bound_host)}
    origin = headers.get("Origin")
    if origin not in allowed:
        raise ForbiddenError(
            f"Origin {origin!r} refused; state-changing endpoints require a "
            "same-origin request — loopback binding does not stop a cross-site "
            "POST from a page the operator visits [PRA-H2]"
        )
    ctype = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        raise ForbiddenError(
            f"Content-Type {ctype!r} refused; state-changing endpoints require "
            "application/json (rejects the text/plain no-cors bypass) [PRA-H2]"
        )
