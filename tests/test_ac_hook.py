"""AC-coverage enforcement hook [XC-2 / REVIEW-D-P6-1].

Reproduce-first: the static checker must *fail* on a planted violation (missing,
misnamed, duplicate, or orphan AC test) and pass on a clean tree. A separate
subprocess check (test_eval_phase6_enforcement) proves the ``conftest`` wiring
aborts collection end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from tests.ac_coverage import check_ac_coverage

_ROOT = Path(__file__).resolve().parents[1]
_SPECS = _ROOT / "docs" / "design" / "specs"
_TESTS = _ROOT / "tests"


def _write_story(specs: Path, tests: Path, n: int, spec_acs: list[int], test_acs: list[int]) -> None:
    acc = "\n".join(f'  - id: "AC-{a}"\n    text: "x"' for a in spec_acs)
    (specs / f"eval{n}.spec.md").write_text(f"acceptance:\n{acc}\n", encoding="utf-8")
    body = "\n\n".join(f"def test_ac{a}_covered():\n    assert True" for a in test_acs)
    (tests / f"test_eval{n}_story.py").write_text(body + "\n", encoding="utf-8")


def test_clean_tree_has_no_violations(tmp_path):
    specs, tests = tmp_path / "specs", tmp_path / "tests"
    specs.mkdir(); tests.mkdir()
    _write_story(specs, tests, 3, [1, 2, 3], [1, 2, 3])
    assert check_ac_coverage(specs, tests) == []


def test_missing_ac_is_a_violation(tmp_path):
    specs, tests = tmp_path / "specs", tmp_path / "tests"
    specs.mkdir(); tests.mkdir()
    _write_story(specs, tests, 3, [1, 2, 3], [1, 3])  # AC-2 uncovered
    v = check_ac_coverage(specs, tests)
    assert any("eval3" in m and "[2]" in m and "no test_ac" in m for m in v), v


def test_misnamed_ac_is_a_violation(tmp_path):
    specs, tests = tmp_path / "specs", tmp_path / "tests"
    specs.mkdir(); tests.mkdir()
    _write_story(specs, tests, 3, [1, 2], [1, 2, 9])  # test names AC-9, spec has 1,2
    v = check_ac_coverage(specs, tests)
    assert any("eval3" in m and "[9]" in m and "not declared" in m for m in v), v


def test_duplicate_ac_name_is_a_violation(tmp_path):
    specs, tests = tmp_path / "specs", tmp_path / "tests"
    specs.mkdir(); tests.mkdir()
    _write_story(specs, tests, 3, [1], [1])
    (tests / "test_eval3_other.py").write_text(
        "def test_ac1_covered():\n    assert True\n", encoding="utf-8"
    )
    v = check_ac_coverage(specs, tests)
    assert any("duplicate AC test name 'test_ac1_covered'" in m for m in v), v


def test_orphan_ac_test_is_a_violation(tmp_path):
    specs, tests = tmp_path / "specs", tmp_path / "tests"
    specs.mkdir(); tests.mkdir()
    _write_story(specs, tests, 3, [1], [1])
    (tests / "test_stray.py").write_text(  # no eval<N> prefix
        "def test_ac7_wandering():\n    assert True\n", encoding="utf-8"
    )
    v = check_ac_coverage(specs, tests)
    assert any("maps to no eval<N> story" in m for m in v), v


def test_live_repo_tree_is_clean():
    # The instrument's own tree must satisfy its own enforcement.
    assert check_ac_coverage(_SPECS, _TESTS) == []
