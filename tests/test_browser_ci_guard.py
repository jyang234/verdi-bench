"""PRA-H5 — the browser-marked CI job cannot green-pass by skipping.

VERDI_REQUIRE_BROWSER turns the fixture's "skip when the stack is absent" into a
loud failure, so a browser-less CI job fails rather than reporting all-green from
all-skipped (the exact gap the audit flagged: 10 UI acceptance criteria that
never executed in CI). Mirrors test_eval_phase7_ci_guard for docker.
"""

from __future__ import annotations

import pytest

import tests.fixtures.browser as browser_mod


def test_require_browser_raises_when_stack_absent(monkeypatch):
    monkeypatch.setenv("VERDI_REQUIRE_BROWSER", "1")
    monkeypatch.setattr(browser_mod, "browser_available", lambda: False)
    with pytest.raises(browser_mod.BrowserRequiredError):
        browser_mod._require_or_skip()


def test_without_require_browser_absence_is_a_plain_skip(monkeypatch):
    monkeypatch.delenv("VERDI_REQUIRE_BROWSER", raising=False)
    monkeypatch.setattr(browser_mod, "browser_available", lambda: False)
    with pytest.raises(pytest.skip.Exception):
        browser_mod._require_or_skip()


def test_paths_are_env_configurable(monkeypatch, tmp_path):
    """The playwright/chromium paths are overridable so CI can point at its own
    layout instead of this dev box's hardcoded paths."""
    pw = tmp_path / "pw"; pw.mkdir()
    cr = tmp_path / "cr"; cr.mkdir()
    monkeypatch.setenv("VERDI_PLAYWRIGHT_PATH", str(pw))
    monkeypatch.setenv("VERDI_CHROMIUM_PATH", str(cr))
    assert browser_mod._playwright_path() == pw
    assert browser_mod._chromium_path() == cr
