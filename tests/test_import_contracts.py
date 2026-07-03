"""The completed import-linter source lists actually catch a violation [XC-5].

Reproduce-first: before Phase 6 the source lists omitted several modules, so an
unlisted module could import a forbidden target undetected. Here we *plant* a
forbidden import into a now-listed module, run the real ``lint-imports``, and
assert the contract breaks — then restore. This proves the completion is
load-bearing, not decorative.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_LINT = Path(sys.executable).parent / "lint-imports"

# (now-listed module, planted import line, forbidden target substring)
_CASES = [
    ("harness/run/redact.py", "import harness.run.engines.harbor", "harbor"),
    ("harness/blind/core.py", "import harness.ledger.chain", "ledger.chain"),
]


def _run_lint() -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_LINT)], cwd=_REPO, capture_output=True, text=True, timeout=120
    )


def test_baseline_contracts_are_green():
    assert _run_lint().returncode == 0, "contracts must be green before planting"


@pytest.mark.parametrize("module, planted, target", _CASES)
def test_completed_contract_catches_planted_import(module, planted, target):
    path = _REPO / module
    original = path.read_text(encoding="utf-8")
    injected = (
        original
        + f"\n\ndef _planted_contract_violation():  # test-injected, restored below\n"
        + f"    {planted}  # noqa\n"
    )
    try:
        path.write_text(injected, encoding="utf-8")
        result = _run_lint()
        assert result.returncode != 0, (
            f"planting {planted!r} in {module} did not break any contract:\n"
            f"{result.stdout}"
        )
        assert "BROKEN" in result.stdout, result.stdout
        assert target in result.stdout, result.stdout
    finally:
        path.write_text(original, encoding="utf-8")
    # Restoration is covered by test_baseline_contracts_are_green; re-running
    # lint-imports here would just re-spawn the slow import-graph walk again.
