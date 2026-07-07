"""Committed groundwork grader-plugin fixtures [verdi-go integration plan §3].

Loaders + provenance note for the planted Go modules and the recorded
``groundwork review --json`` outputs under ``tests/fixtures/groundwork/``.

PROVENANCE. The JSON fixtures in ``groundwork/json/`` were GENERATED once by
running the real, pinned ``flowmap`` + ``groundwork`` binaries on the planted
``invsvc`` (reach-trap) and ``alertsvc`` (blind-spot) modules — see
``tests/fixtures/groundwork/regen.sh`` for the exact commands. Volatile provenance
fields were stripped so the committed fixtures are byte-stable across flowmap
builds and pin only the SEMANTIC fields the mapper reads:

* graphs: the ``tool`` producing-build field is stripped (kept: ``stamp``).
* review JSON: ``digest`` (recomputes from the exact graph bytes) and
  ``algo`` / ``caveats`` (substrate provenance) are stripped.

``review_unknown.json`` is HAND-WRITTEN (a synthetic future verdict groundwork
does not emit today) to exercise the unknown-verdict → abstain path.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "groundwork"
INVSVC_DIR = FIXTURES_DIR / "invsvc"
JSON_DIR = FIXTURES_DIR / "json"


def load_review(name: str) -> dict:
    """Load a committed ``groundwork review --json`` fixture by short name.

    ``name`` is the stem, e.g. ``"block"`` for ``json/review_block.json``."""
    return json.loads((JSON_DIR / f"review_{name}.json").read_text(encoding="utf-8"))
