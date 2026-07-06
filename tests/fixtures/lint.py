"""Run the repository's real import-linter from tests [refactor 01 §2].

One runner for every contract-is-load-bearing test (the XC-5 plant pattern):
the suites plant a forbidden import and assert ``lint-imports`` breaks. The
``cwd`` parameter serves the throwaway-shadow-tree variant, which lints a
copied tree so a hard kill can never leave a plant in the live source [F-L11].
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_LINT = Path(sys.executable).parent / "lint-imports"


def run_lint(cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_LINT)], cwd=cwd, capture_output=True, text=True, timeout=120
    )
