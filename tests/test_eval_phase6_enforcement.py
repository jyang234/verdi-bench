"""Phase 6 exit — the enforcement mechanisms hold end-to-end.

The per-slice reproduce-first tests already pin each mechanism against a planted
violation (test_ac_hook, test_import_contracts, test_analyze_ci, test_eval2_client,
test_eval4_insulation, test_eval3_power). This file adds only what those do not
cover: the end-to-end proof that the shipped conftest actually *aborts collection*
on a planted AC-coverage violation (not merely that the checker function reports
it), plus two cheap invariants with no other home (the completed contract lists
cover the newly-listed modules; the dead symbols are gone).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.ac_coverage import check_ac_coverage

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
_SPECS = _REPO / "docs" / "design" / "specs"


# --- XC-2: the AC hook enforces end-to-end (aborts collection) ---------------
def test_ac_hook_aborts_real_collection_on_planted_violation():
    """A planted test naming an AC its story's spec does not declare makes the
    shipped conftest fail collection with a nonzero exit — the enforcing gate,
    not just the checker function."""
    planted = _TESTS / "test_eval3_planted_zzz.py"
    planted.write_text("def test_ac99_planted():\n    assert True\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--co", "-q", "-p", "no:cacheprovider",
             "tests/test_eval3_power.py"],
            cwd=_REPO, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert "AC-coverage enforcement failed" in (result.stdout + result.stderr)
    finally:
        planted.unlink(missing_ok=True)
    # and the tree is clean again once the planted file is gone
    assert check_ac_coverage(_SPECS, _TESTS) == []


# --- XC-5: the completed import-contract lists cover the newly-listed modules -
def test_import_contract_lists_cover_new_modules():
    """The source lists include the modules the review found omitted. (That a
    planted forbidden import is actually caught is proved in test_import_contracts;
    this is the cheap static coverage check, no subprocess.)"""
    cfg = (_REPO / ".importlinter").read_text()
    for mod in ("harness.cli", "harness.entrypoints", "harness.version",
                "harness.run.redact", "harness.run.settings", "harness.blind"):
        assert mod in cfg, f"{mod} missing from an import contract source list"


# --- RN-18: the dead symbols are gone ----------------------------------------
def test_dead_symbols_removed():
    from harness.adapters.base import Outcome
    from harness.run.budget import CostGuard

    assert not hasattr(Outcome, "not_started_cost_ceiling")
    assert "stopped" not in set(CostGuard.__dataclass_fields__)
