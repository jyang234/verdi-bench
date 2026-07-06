"""The operator dashboard page [EVAL-13 AC-6; EVAL-14 AC-3..AC-5; EVAL-19 AC-2..AC-5].

One self-contained HTML document (D001: dependency-free single-file app):
inline CSS, inline script, relative ``fetch('/api/…')`` calls only — no
external URI schemes, no fetched assets, no href/src/link/@import/url()
references. Navigation out (the dossier artifact) uses scripted
``window.open`` on relative paths, never an anchor, so the needle property
holds verbatim. All dynamic values land via ``textContent`` — ledger strings
are data, never markup. Inline SVG (the cost sparklines) is created through
the namespace of a static ``<svg>`` prototype in the document, so no
namespace URI string ever appears in the page bytes.

Structure: a hash router over six screens (workspace home, experiment
overview, trials, trial detail, compare, findings). Every view state that
matters lives in the URL, so a link reproduces the exact slice [AC-3].
``window.__vb()`` exposes a small read-only state snapshot as an explicit test
seam for the headless AC drives — it is not an API.

``const BUNDLE = null;`` is the static-export seam [EVAL-19 AC-1]:
``harness.serve.bundle.write_bundle`` replaces that one line with the archived
data object, turning the same document into a no-server snapshot.

The page is the **openly-unblinded operator tier** and says so on every render
[EVAL-13 D003].

[refactor 07 §4] The markup/CSS template (``static/page.html``) and the SPA
script (``static/app.js``) are lintable package-data files, composed into
``OPERATOR_PAGE`` at import time via the tier-neutral :mod:`harness.webkit.page`
inliner (decision P5-JS). The served document is byte-equivalent to the former
inline string — the ``.js`` file carries real non-ASCII characters instead of
``\\uXXXX`` escapes, which parse identically — and still makes zero external
requests. The operator surface keeps its own richer token palette and its own
bundle-aware ``h()``/``j()`` (it does not share the reviewer/author kit), so
only the app-script splice point is used here.
"""

from __future__ import annotations

from pathlib import Path

from ..webkit import page as webkit_page

_STATIC = Path(__file__).resolve().parent / "static"

OPERATOR_PAGE = webkit_page.compose(
    (_STATIC / "page.html").read_text(encoding="utf-8"),
    (_STATIC / "app.js").read_text(encoding="utf-8"),
)
