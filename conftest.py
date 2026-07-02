"""Program-wide pytest configuration.

Implements the M0 test-naming convention hook [master plan §3.5]: AC-mapped
tests are named ``test_ac<N>_*`` so AC coverage is recomputable mechanically.
The hook collects the set of AC numbers exercised and, with ``--ac-report``,
prints them at session end.
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
    for item in items:
        m = _AC_RE.search(item.name)
        if m:
            _seen_acs.add(m.group(1))


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001
    if config.getoption("--ac-report"):
        acs = sorted(_seen_acs, key=int)
        terminalreporter.write_line(f"AC coverage: {', '.join('AC-' + a for a in acs)}")
