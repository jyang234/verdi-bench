"""The reviewer queue page [EVAL-18 AC-4, D003].

One self-contained document (the house needle property, inline script
allowed) whose whole vocabulary is blinded: comparisons, Response 1/2, and
the reviewer's own answers. It carries the inverse of the operator banner —
the standing instruction that a reviewer must not open the operator view for
experiments they review — and never renders an arm name before the ledgered
reveal for that comparison returns one.

Keyboard-first capture (D003, the parity-research queue ergonomics mapped to
our verdict vocabulary): 1 / 2 / T / C pick the winner, the two integrity
answers are required before submit enables, Enter records and advances,
j/k move the queue. ``window.__vb()`` is the headless-test seam.

[refactor 07 §4] The markup/CSS template (``static/page.html``) and the SPA
script (``static/app.js``) are lintable package-data files (decision P5-JS).
The shared design-token CSS and the ``h()``/``j()`` DOM/fetch kit come from the
tier-neutral :mod:`harness.webkit.page` — the reviewer surface imports webkit,
never ``harness.serve``/``author`` (the isolation contract holds: webkit reaches
no peer surface). The composed document is byte-equivalent to the former inline
string; the blinded banner and every reviewer-vocabulary literal stay here.
"""

from __future__ import annotations

from pathlib import Path

from ..webkit import page as webkit_page

_STATIC = Path(__file__).resolve().parent / "static"

REVIEWER_PAGE = webkit_page.compose(
    (_STATIC / "page.html").read_text(encoding="utf-8"),
    webkit_page.splice_kit((_STATIC / "app.js").read_text(encoding="utf-8")),
    tokens_css=webkit_page.TOKENS_CSS,
)
