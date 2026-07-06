"""Grader-output fixture for the fake-engine grade path [refactor 01 §2].

``holdout_results.json`` is a public grade seam — the local runner's input,
the same shape scripts/shakedown's ``inject_grades`` speaks — so tests write
it through ONE writer and the shape cannot drift per file. Deliberately
malformed or forged variants (empty assertion lists, junk bytes, benchmark
shim output) stay literal at their sites: those tests are about the exact
bytes, not the seam.
"""

from __future__ import annotations

import json
from pathlib import Path


def write_holdout_results(workspace, passed, *, assertion_id="h1") -> dict:
    """Write the single-assertion grader output into ``workspace``.

    Returns the payload so tests asserting on the exact content (e.g. the
    forged-evidence protections) compare against what was really written.
    """
    payload = {"assertions": [{"id": assertion_id,
                               "result": "pass" if passed else "fail"}]}
    (Path(workspace) / "holdout_results.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload
