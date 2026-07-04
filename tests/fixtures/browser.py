"""Headless page-drive helper for the EVAL-14 UI acceptance tests.

Drives the served operator page in the pre-installed Chromium through the
globally installed node playwright — no Python browser dependency. When the
environment lacks the stack (node, playwright, or the browser binary) the
test SKIPS honestly, exactly like the ``docker``-marked suite skips without
a daemon; it never fakes a pass. The driver script gets ``page`` (a
playwright Page), ``BASE`` (the server URL), and an ``out`` object it must
fill; whatever lands in ``out`` comes back as the parsed result, plus
``__errors`` collecting page/console errors.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_MARKER = "@@RESULT@@"


class BrowserRequiredError(RuntimeError):
    """VERDI_REQUIRE_BROWSER is set but the node/playwright/chromium stack is not
    available [PRA-H5]. Mirrors DockerRequiredError: a browser-marked CI job must
    not green-pass by skipping every UI acceptance test."""


def _playwright_path() -> Path:
    """The node playwright module. Env-configurable [PRA-H5] so CI (or any host)
    can point at its own layout instead of this dev box's hardcoded paths — the
    reason the UI AC tests silently skipped everywhere but here."""
    env = os.environ.get("VERDI_PLAYWRIGHT_PATH")
    if env:
        return Path(env)
    return Path("/opt/node22/lib/node_modules/playwright")


def _chromium_path() -> Path:
    """The Chromium binary/dir. Honors VERDI_CHROMIUM_PATH, then Playwright's own
    PLAYWRIGHT_BROWSERS_PATH, then the dev-box default [PRA-H5]."""
    env = os.environ.get("VERDI_CHROMIUM_PATH")
    if env:
        return Path(env)
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers:
        cand = Path(browsers) / "chromium"
        if cand.exists():
            return cand
    return Path("/opt/pw-browsers/chromium")


def browser_available() -> bool:
    return (
        shutil.which("node") is not None
        and _playwright_path().exists()
        and _chromium_path().exists()
    )


def _require_or_skip() -> None:
    """Skip when the browser stack is absent — UNLESS VERDI_REQUIRE_BROWSER is set
    (the CI browser job), in which case raise so the job cannot green-pass by
    skipping every UI AC test [PRA-H5], mirroring VERDI_REQUIRE_DOCKER."""
    if browser_available():
        return
    if os.environ.get("VERDI_REQUIRE_BROWSER"):
        raise BrowserRequiredError(
            "VERDI_REQUIRE_BROWSER is set but node/playwright/chromium is not "
            "available; the browser-marked UI acceptance tests must not "
            "green-pass by skipping. Provision the stack (node + the playwright "
            "npm package + a Chromium build) or unset VERDI_REQUIRE_BROWSER, and "
            "point VERDI_PLAYWRIGHT_PATH / VERDI_CHROMIUM_PATH at them [PRA-H5]."
        )
    pytest.skip("browser drive unavailable (node/playwright/chromium not present)")


def drive(base_url: str, body_js: str, tmp_path: Path, *, timeout_s: int = 120) -> dict:
    """Run ``body_js`` against a live page; return the ``out`` object."""
    _require_or_skip()
    _PLAYWRIGHT = _playwright_path()
    _CHROMIUM = _chromium_path()
    script = (
        "const { chromium } = require(" + json.dumps(str(_PLAYWRIGHT)) + ");\n"
        "(async () => {\n"
        "  const browser = await chromium.launch({ executablePath: "
        + json.dumps(str(_CHROMIUM))
        + " });\n"
        "  const page = await (await browser.newContext({ viewport: { width: 1200, height: 900 } })).newPage();\n"
        "  const errors = [];\n"
        "  page.on('pageerror', e => errors.push('pageerror: ' + e.message));\n"
        "  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });\n"
        "  const out = {};\n"
        "  const BASE = " + json.dumps(base_url) + ";\n"
        + body_js
        + "\n  out.__errors = errors;\n"
        "  console.log(" + json.dumps(_MARKER) + " + JSON.stringify(out));\n"
        "  await browser.close();\n"
        "})().catch(e => { console.log(" + json.dumps(_MARKER)
        + " + JSON.stringify({ __fatal: String(e && e.stack || e) })); process.exit(1); });\n"
    )
    path = tmp_path / "drive.cjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, timeout=timeout_s
    )
    for line in proc.stdout.splitlines():
        if line.startswith(_MARKER):
            result = json.loads(line[len(_MARKER):])
            assert "__fatal" not in result, f"browser drive failed: {result['__fatal']}"
            return result
    raise AssertionError(
        f"browser drive produced no result marker.\nstdout: {proc.stdout!r}\n"
        f"stderr: {proc.stderr!r}"
    )
