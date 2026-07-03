"""Program-wide pytest configuration.

Implements the M0 test-naming convention hook [master plan §3.5]: AC-mapped
tests are named ``test_ac<N>_*`` so AC coverage is recomputable mechanically.

Two layers:

* **Enforcement (unconditional)** — at collection, :func:`check_ac_coverage`
  statically verifies that every story's pre-registered ACs are covered by a
  ``test_ac<N>_*`` test, with no missing/misnamed/duplicate AC test [XC-2 /
  REVIEW-D-P6-1]. A violation fails the session loudly; a regression that drops
  or renames an AC test can no longer pass green.
* **Reporting (``--ac-report``)** — prints the AC numbers exercised by the
  collected tests at session end, a convenience layered on top.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable so ``import harness`` and ``tests.fixtures``
# resolve regardless of the invoking cwd.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.ac_coverage import check_ac_coverage  # noqa: E402  (needs _ROOT on sys.path)

_SPECS_DIR = _ROOT / "docs" / "design" / "specs"
_TESTS_DIR = _ROOT / "tests"

_AC_RE = re.compile(r"test_ac(\d+)_")
_seen_acs: set[str] = set()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ac-report",
        action="store_true",
        default=False,
        help="Print the set of AC numbers exercised by collected tests.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    violations = check_ac_coverage(_SPECS_DIR, _TESTS_DIR)
    if violations:
        raise pytest.UsageError(
            "AC-coverage enforcement failed [XC-2]:\n  - " + "\n  - ".join(violations)
        )
    for item in items:
        m = _AC_RE.search(item.name)
        if m:
            _seen_acs.add(m.group(1))


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001
    if config.getoption("--ac-report"):
        acs = sorted(_seen_acs, key=int)
        terminalreporter.write_line(f"AC coverage: {', '.join('AC-' + a for a in acs)}")
