"""README claims that are mechanically checkable stay true [XC-7].

Phase 6's ethos is that a claim about the instrument is backed by an enforcing
check. The import-contract count is a low-churn, load-bearing claim, so pin it:
the README's "N import-linter contracts" must match the number of contracts
actually declared in ``.importlinter``.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_readme_contract_count_matches_importlinter():
    live = len(
        re.findall(r"^\[importlinter:contract:", (_REPO / ".importlinter").read_text(),
                   flags=re.MULTILINE)
    )
    readme = (_REPO / "README.md").read_text()
    m = re.search(r"(\d+)\s+import-linter contracts", readme)
    assert m, "README no longer states an import-linter contract count"
    assert int(m.group(1)) == live, (
        f"README claims {m.group(1)} import-linter contracts but .importlinter "
        f"declares {live}"
    )
