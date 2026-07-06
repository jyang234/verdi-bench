"""The shared page-kit chunks compose byte-exactly [refactor 07 §4].

Pins the mechanical Layer-2 helpers: the design-token CSS and the ``h()``/``j()``
kit are the single source (real package-data files, ASCII), and the compose /
splice helpers fail loudly on a missing or doubled splice point rather than
emit a half-built or double-embedded page.
"""

from __future__ import annotations

import pytest

from harness.webkit import page as webkit_page


def test_shared_chunks_are_single_source_ascii_package_data():
    # design tokens: the :root custom-property block, real .css, ASCII
    assert webkit_page.TOKENS_CSS.isascii()
    assert ":root {" in webkit_page.TOKENS_CSS
    assert "--surface-1: #fcfcfb" in webkit_page.TOKENS_CSS
    assert "prefers-color-scheme: dark" in webkit_page.TOKENS_CSS
    # the DOM builder + fetch wrapper: real .js, ASCII, the two mechanical fns
    assert webkit_page.KIT_JS.isascii()
    assert "async function j(url, opts) {" in webkit_page.KIT_JS
    assert "function h(tag, props, ...kids) {" in webkit_page.KIT_JS
    # the kit is a valid fragment (no stray splice marker inside itself)
    assert webkit_page.KIT_SLOT not in webkit_page.KIT_JS


def test_splice_kit_inserts_once_at_the_slot():
    app = "before;\n" + webkit_page.KIT_SLOT + "\nafter;"
    out = webkit_page.splice_kit(app)
    assert webkit_page.KIT_JS in out
    assert webkit_page.KIT_SLOT not in out
    assert out == "before;\n" + webkit_page.KIT_JS + "\nafter;"


def test_splice_kit_fails_loudly_without_exactly_one_slot():
    with pytest.raises(ValueError, match="kit splice point"):
        webkit_page.splice_kit("no slot here")
    with pytest.raises(ValueError, match="kit splice point"):
        webkit_page.splice_kit(webkit_page.KIT_SLOT + webkit_page.KIT_SLOT)


def test_compose_inlines_app_and_optional_tokens():
    tmpl = "<style>\n__TOKENS_CSS__\n</style><script>\n__APP_JS__\n</script>"
    out = webkit_page.compose(tmpl, "APPCODE", tokens_css="TOKENS")
    assert out == "<style>\nTOKENS\n</style><script>\nAPPCODE\n</script>"
    # without tokens (the operator surface keeps its own): only the app is inlined
    out2 = webkit_page.compose("<script>\n__APP_JS__\n</script>", "X")
    assert out2 == "<script>\nX\n</script>"


def test_compose_refuses_missing_or_doubled_slots():
    with pytest.raises(ValueError, match="app-script splice point"):
        webkit_page.compose("<script></script>", "X")
    with pytest.raises(ValueError, match="tokens splice point"):
        webkit_page.compose("__APP_JS__ __APP_JS__", "X", tokens_css="T")
    with pytest.raises(ValueError, match="app-script splice point"):
        webkit_page.compose("__APP_JS__ __APP_JS__ __TOKENS_CSS__", "X", tokens_css="T")


def test_shared_chunks_are_live_single_source_on_reviewer_and_author():
    """The dedup is load-bearing, not decorative: the reviewer and author
    documents carry the webkit token block and DOM/fetch kit verbatim (one
    source), while the operator surface legitimately keeps its own richer kit
    and palette — so a change to webkit's KIT_JS moves both mutation surfaces
    at once and cannot silently touch the operator tier."""
    from harness.author.page import AUTHOR_PAGE
    from harness.review.serve_page import REVIEWER_PAGE
    from harness.serve.page import OPERATOR_PAGE

    for page in (REVIEWER_PAGE, AUTHOR_PAGE):
        assert page.count(webkit_page.TOKENS_CSS) == 1
        assert page.count(webkit_page.KIT_JS) == 1
    # the operator surface does NOT share them (its h()/j() are bundle-aware /
    # display-only and its tokens carry the meter/spark/diff palette)
    assert webkit_page.KIT_JS not in OPERATOR_PAGE
    assert webkit_page.TOKENS_CSS not in OPERATOR_PAGE
