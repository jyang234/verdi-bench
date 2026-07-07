"""The shared refusal base every stage verb maps uniformly [refactor 13 OI-B].

A stdlib-only leaf: it imports nothing from ``harness`` so every subsystem can
reparent its refusal family onto it without a cycle. :class:`VerdiRefusal` is a
mixin-style base — a subsystem's refusal keeps its existing root (``ValueError``,
``RuntimeError``, a family base) and merely gains ``VerdiRefusal`` alongside it,
so no ``isinstance`` check or pinned message changes.

Contract: carrying a :class:`VerdiRefusal` means "this operation refused for a
stated reason". ``str(err)`` is the operator-facing refusal text — the exact
line a verb echoes to stderr before exiting 2. It is never an internal bug: a
genuine defect must surface as its own uncaught exception (a loud traceback),
never masqueraded as a clean refusal. This is what lets ``cli_common.refusal_exit``
catch the base uniformly — a refusal a verb forgot to enumerate becomes a clean
named exit 2 instead of a raw traceback, completing the fail-loud story.
"""

from __future__ import annotations


class VerdiRefusal(Exception):
    """Mixin-style base for every operator-facing refusal [refactor 13 OI-B].

    Subtypes reparent at their family base (``class SpecError(VerdiRefusal,
    ValueError)``) so leaf refusals inherit transitively. ``str(self)`` is the
    stated reason the operator sees; a ``VerdiRefusal`` is never raised for an
    internal invariant violation."""
