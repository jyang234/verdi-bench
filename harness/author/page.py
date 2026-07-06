"""The authoring page [EVAL-17 AC-4, AC-5; D002].

One self-contained HTML document (the operator page's discipline: inline
CSS + script, relative fetches only, no external references, both themes).
The editable text panes are canonical (D002): the wizard's template button
generates YAML into the pane once, Save writes those exact bytes, and every
preview — validation, power, schedule, sha — is a GET over what Save wrote.
The sha shown beside the Lock button is computed from the same bytes the
lock will hash; the ceremony displays it before asking for attestation.

A locked draft renders read-only: panes disabled, no Save, no Lock — the
immutability of pre-registration as a visible fact [AC-3].

[refactor 07 §4] The markup/CSS template (``static/page.html``) and the SPA
script (``static/app.js``) are lintable package-data files, composed into
``AUTHOR_PAGE`` via the tier-neutral :mod:`harness.webkit.page` (decision
P5-JS): the shared design-token CSS and the ``h()``/``j()`` kit are spliced in
from webkit, and the template pane's ``__STARTER_SPEC_JSON__`` slot is filled
from the ONE canonical starter spec [refactor 02 §2] — read directly as a data
file, since the sdk-is-a-leaf contract forbids importing ``harness.sdk``. The
composed document is byte-equivalent to the former inline string.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..webkit import page as webkit_page

_STATIC = Path(__file__).resolve().parent / "static"

# The template pane is seeded from the ONE canonical starter spec [refactor 02
# §2] so it can never drift from the docs example / test builders again. The
# sdk-is-a-leaf import contract forbids importing the sdk package, so the shared
# template DATA file is read directly — the file is the contract, not the code.
_STARTER_SPEC_JSON = json.dumps(
    (
        Path(__file__).resolve().parent.parent
        / "sdk" / "templates" / "starter-experiment.yaml"
    ).read_text(encoding="utf-8")
)

_app_js = webkit_page.splice_kit((_STATIC / "app.js").read_text(encoding="utf-8"))
_app_js = _app_js.replace("__STARTER_SPEC_JSON__", _STARTER_SPEC_JSON)

AUTHOR_PAGE = webkit_page.compose(
    (_STATIC / "page.html").read_text(encoding="utf-8"),
    _app_js,
    tokens_css=webkit_page.TOKENS_CSS,
)
