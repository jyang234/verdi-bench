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
import shutil
import subprocess
from pathlib import Path

import pytest

_PLAYWRIGHT = Path("/opt/node22/lib/node_modules/playwright")
_CHROMIUM = Path("/opt/pw-browsers/chromium")
_MARKER = "@@RESULT@@"


def browser_available() -> bool:
    return (
        shutil.which("node") is not None
        and _PLAYWRIGHT.exists()
        and _CHROMIUM.exists()
    )


def drive(base_url: str, body_js: str, tmp_path: Path, *, timeout_s: int = 120) -> dict:
    """Run ``body_js`` against a live page; return the ``out`` object."""
    if not browser_available():
        pytest.skip("browser drive unavailable (node/playwright/chromium not present)")
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
