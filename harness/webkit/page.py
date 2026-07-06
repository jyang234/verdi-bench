"""Layer 2 — the shared mechanical page chunks [refactor 07 §4].

The three surfaces each ship ONE self-contained HTML document (inline CSS +
inline script, relative fetches only, no external references). The *product*
content — banners, screen structure, blinded-vs-unblinded wording — is
per-surface and never shared. The *mechanical* chunks were copy-maintained:

- the design-token custom properties (:data:`TOKENS_CSS`), byte-identical on the
  reviewer and author surfaces (the operator surface carries a superset for its
  data-viz palette and keeps its own);
- the ``h()`` DOM builder + ``j()`` fetch wrapper (:data:`KIT_JS`), byte-identical
  on those same two surfaces.

Each surface owns its ``static/page.html`` + ``static/app.js`` (per-surface data,
so the reviewer-isolation contract holds for page *content* too — the operator's
unblinded strings never live where the reviewer document is assembled). This
module composes the shared chunks into those templates at import time via
:func:`compose` (used by all three) and :func:`splice_kit` (the two surfaces that
share the DOM/fetch kit). The JS lives in real ``.js`` package-data files rather
than escaped Python strings (decision P5-JS): lintable and diffable, inlined at
serve/bundle time so the served document still makes zero external requests.

Composition is byte-*equivalent* to the former inline strings where it is pinned
(bundle marker present exactly once, no external-URI needles) — the ``.js`` files
carry real non-ASCII characters (``·`` ``→`` ``—``) instead of ``\\uXXXX`` escapes,
which parse to the identical string values, so the rendered DOM is unchanged.
"""

from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).resolve().parent / "static"

# The design-token block (:root + the dark-scheme override) shared verbatim by
# the reviewer and author surfaces. Composed into each <style> at TOKENS_SLOT.
TOKENS_CSS = (_STATIC / "tokens.css").read_text(encoding="utf-8")

# The DOM builder + fetch wrapper shared verbatim by those two surfaces. Spliced
# into each app.js at KIT_SLOT (kept where the surfaces already declared it, so
# the composed script is byte-equivalent to the former inline copy).
KIT_JS = (_STATIC / "kit.js").read_text(encoding="utf-8")

# Splice points a per-surface template/script reserves for the shared chunks.
APP_SLOT = "__APP_JS__"       # in page.html: where the surface's app.js is inlined
TOKENS_SLOT = "__TOKENS_CSS__"  # in page.html <style>: where TOKENS_CSS is inlined
KIT_SLOT = "/*@@KIT@@*/"       # in app.js: where KIT_JS is spliced (a valid JS comment)


def _require_one(text: str, marker: str, what: str) -> None:
    n = text.count(marker)
    if n != 1:
        raise ValueError(
            f"expected exactly one {marker!r} {what} to compose, found {n} "
            "— refusing to assemble a page that would not embed its chunk"
        )


def splice_kit(app_js: str) -> str:
    """Insert the shared :data:`KIT_JS` where a per-surface ``app.js`` reserves
    :data:`KIT_SLOT` — the reviewer/author DOM/fetch kit, sourced once."""
    _require_one(app_js, KIT_SLOT, "kit splice point")
    return app_js.replace(KIT_SLOT, KIT_JS)


def compose(page_html: str, app_js: str, *, tokens_css: str | None = None) -> str:
    """Assemble one self-contained document: inline ``app_js`` at
    :data:`APP_SLOT`, and (when a surface shares them) ``tokens_css`` at
    :data:`TOKENS_SLOT`. Fails loudly if a splice point is missing or doubled —
    a page that would not embed its script is refused, never served half-built.
    """
    if tokens_css is not None:
        _require_one(page_html, TOKENS_SLOT, "tokens splice point")
        page_html = page_html.replace(TOKENS_SLOT, tokens_css)
    _require_one(page_html, APP_SLOT, "app-script splice point")
    return page_html.replace(APP_SLOT, app_js)
