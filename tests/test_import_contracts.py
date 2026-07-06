"""The completed import-linter source lists actually catch a violation [XC-5].

Reproduce-first: before Phase 6 the source lists omitted several modules, so an
unlisted module could import a forbidden target undetected. Here we *plant* a
forbidden import into a now-listed module, run the real ``lint-imports``, and
assert the contract breaks — then restore. This proves the completion is
load-bearing, not decorative.
"""

from __future__ import annotations

import pytest

from tests.fixtures.lint import REPO, run_lint

# (now-listed module, planted import line, forbidden target substring)
_CASES = [
    ("harness/run/redact.py", "import harness.run.engines.harbor", "harbor"),
    ("harness/blind/core.py", "import harness.ledger.chain", "ledger.chain"),
    # F-M-T3: the reviewer surface must not grow an LLM client
    ("harness/review/scrub.py", "import harness.judge.client", "judge.client"),
]


def test_baseline_contracts_are_green():
    assert run_lint().returncode == 0, "contracts must be green before planting"


def test_ledger_contract_source_list_covers_every_harness_package():
    """PRA-L6: the ledger contract's source list is hand-maintained, so a new
    top-level harness package/module could import ledger.chain undetected until
    someone remembers to add it. Assert the list is complete — every top-level
    harness entry (except the ledger package itself, the contract's owner) is a
    source, so the fail-open gap is closed with a mechanical check."""
    import re

    harness_dir = REPO / "harness"
    live: set[str] = set()
    for p in harness_dir.iterdir():
        if p.name in ("__init__.py", "__pycache__", "ledger"):
            continue
        if p.is_dir() and (p / "__init__.py").exists():
            live.add(f"harness.{p.name}")
        elif p.suffix == ".py":
            live.add(f"harness.{p.stem}")

    text = (REPO / ".importlinter").read_text()
    block = text.split("Ledger appends flow only through typed constructors", 1)[1]
    block = block.split("forbidden_modules", 1)[0]
    listed = set(re.findall(r"harness\.[A-Za-z0-9_]+", block))
    missing = live - listed
    assert not missing, (
        f"ledger import contract omits harness package(s) {sorted(missing)}; a "
        "module absent from the source list could import ledger.chain undetected"
    )


@pytest.mark.parametrize("module, planted, target", _CASES)
def test_completed_contract_catches_planted_import(module, planted, target, tmp_path):
    """F-L11: the plant lands in a THROWAWAY copy of the tree, never the live
    source — the old plant-then-restore-in-finally left the forbidden import
    in the working tree on a hard kill (SIGKILL skips finally)."""
    import shutil

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    shutil.copytree(REPO / "harness", shadow / "harness")
    shutil.copy(REPO / ".importlinter", shadow / ".importlinter")
    path = shadow / module
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"\n\ndef _planted_contract_violation():  # test-injected, throwaway tree\n"
        + f"    {planted}  # noqa\n",
        encoding="utf-8",
    )
    result = run_lint(cwd=shadow)
    assert result.returncode != 0, (
        f"planting {planted!r} in {module} did not break any contract:\n"
        f"{result.stdout}"
    )
    assert "BROKEN" in result.stdout, result.stdout
    assert target in result.stdout, result.stdout
