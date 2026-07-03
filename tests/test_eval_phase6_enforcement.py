"""Phase 6 exit — each enforcement mechanism fails on a planted violation.

The ordered proof that the holes Phase 6 closed cannot silently reopen. Most
mechanisms have their own reproduce-first tests (test_ac_hook, test_import_
contracts, test_analyze_ci, test_eval4_insulation, test_eval3_power); this gathers
the load-bearing invariants and adds the one end-to-end proof not covered
elsewhere: that the shipped conftest actually *aborts collection* on a planted
AC-coverage violation (not merely that the checker function reports it).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

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


# --- XC-5: the completed import contracts + seam test enforce ----------------
def test_import_contracts_are_complete_and_green():
    """Both source lists cover cli/entrypoints/version and the run submodules, and
    all three contracts are green (a planted violation is caught by
    test_import_contracts)."""
    cfg = (_REPO / ".importlinter").read_text()
    for mod in ("harness.cli", "harness.entrypoints", "harness.version",
                "harness.run.redact", "harness.run.settings", "harness.blind"):
        assert mod in cfg, f"{mod} missing from an import contract source list"
    lint = subprocess.run(
        [str(Path(sys.executable).parent / "lint-imports")],
        cwd=_REPO, capture_output=True, text=True, timeout=120,
    )
    assert lint.returncode == 0, lint.stdout


# --- RN-18: the fake provider fails loud -------------------------------------
def test_fake_provider_exhaustion_is_loud():
    from harness.judge.providers.fake import FakeProvider, FakeProviderExhausted

    prov = FakeProvider(["one"])
    prov.complete("m", [{"content": "x"}], 0.0)
    try:
        prov.complete("m", [{"content": "x"}], 0.0)
    except FakeProviderExhausted:
        pass
    else:  # pragma: no cover - exhaustion must raise
        raise AssertionError("FakeProvider replayed instead of raising")


# --- AN-11: the CI edges compute the corrected value -------------------------
def test_an11_ci_edges_corrected():
    from harness.analyze.ci import BCaCI, ClusterRobustTCI, PercentileCI

    # BCa mid-p: symmetric tie-heavy distribution => z0 == 0 => symmetric interval.
    deltas = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    boot_means = np.concatenate([np.linspace(-1.0, 1.0, 201), np.zeros(100)])
    lo, hi = BCaCI().interval(deltas, boot_means, np.ones_like(boot_means), 0.95)
    assert abs(lo + hi) < 1e-9

    # ClusterRobustTCI: near-total zero-SE degeneracy => transparent percentile fallback.
    d = np.array([0.1, -0.2, 0.3, 0.0, 0.15])
    bm = np.linspace(-0.3, 0.4, 200)
    ses = np.where(np.arange(200) < 185, 0.0, 0.05)
    with pytest.warns(UserWarning, match="zero SE"):  # the drop is disclosed
        got = ClusterRobustTCI().interval(d, bm, ses, 0.95)
    assert got == PercentileCI().interval(d, bm, ses, 0.95)


# --- dead symbols are gone (RN-18) -------------------------------------------
def test_dead_symbols_removed():
    from harness.adapters.base import Outcome
    from harness.run.budget import CostGuard

    assert not hasattr(Outcome, "not_started_cost_ceiling")
    assert "stopped" not in {f for f in CostGuard.__dataclass_fields__}
