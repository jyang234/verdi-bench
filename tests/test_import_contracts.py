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
    # G4 [refactor 11 §G4]: the sdk-is-a-leaf list BITES — a subsystem importing
    # the sdk facade (the import direction reversed) turns the contract red. corpus
    # is in the sdk-leaf source list and is a source of no strict llm-free contract,
    # so the plant breaks only sdk-is-a-leaf, not another contract by accident.
    ("harness/corpus/registry.py", "import harness.sdk", "harness.sdk"),
]


def test_baseline_contracts_are_green():
    assert run_lint().returncode == 0, "contracts must be green before planting"


# The contracts whose source list must enumerate EVERY harness package except the
# forbidden target's own top-level package (a contract cannot forbid a package from
# importing itself). Each pairs a unique .importlinter name substring with that
# excluded top-level dir name [refactor 11 §G4].
_ALL_PACKAGE_CONTRACTS = [
    ("Ledger appends flow only through typed constructors", "ledger"),
    ("The SDK facade is a leaf consumer", "sdk"),
    ("Harbor is imported only by the run engine seam", "run"),
]


@pytest.mark.parametrize("anchor, target_owner", _ALL_PACKAGE_CONTRACTS)
def test_all_packages_contract_source_list_is_complete(anchor, target_owner):
    """PRA-L6 generalized [refactor 11 §G4]: each all-packages contract's source
    list is hand-maintained, so a new top-level harness package could import the
    forbidden target undetected until someone remembers to add it. Assert every
    discovered top-level harness entry (except the target's own top-level package)
    is a source — closing the fail-open gap with a mechanical check for the
    ledger-writes, sdk-is-a-leaf, AND harbor-confined lists, not the ledger alone.

    A decomposed ``harness.run`` submodule (the harbor list names
    ``harness.run.seam`` etc. so it can forbid only ``harness.run.engines.harbor``)
    collapses to ``harness.run`` in the top-level scan, which is exactly the harbor
    target's excluded owner — so the decomposition stays invisible here."""
    import re

    harness_dir = REPO / "harness"
    live: set[str] = set()
    for p in harness_dir.iterdir():
        if p.name in ("__init__.py", "__pycache__", target_owner):
            continue
        if p.is_dir() and (p / "__init__.py").exists():
            live.add(f"harness.{p.name}")
        elif p.suffix == ".py":
            live.add(f"harness.{p.stem}")

    text = (REPO / ".importlinter").read_text()
    block = text.split(anchor, 1)[1]
    block = block.split("forbidden_modules", 1)[0]
    # Only the source_modules region (drop the name/comment header, which may
    # itself name a harness module in prose).
    block = block.split("source_modules", 1)[1]
    listed = set(re.findall(r"harness\.[A-Za-z0-9_]+", block))
    missing = live - listed
    assert not missing, (
        f"the {anchor!r} import contract omits harness package(s) {sorted(missing)}; "
        "a module absent from the source list could import the forbidden target "
        "undetected [refactor 11 §G4]"
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
