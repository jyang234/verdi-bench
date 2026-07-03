"""Per-story AC-coverage enforcement [master plan §3.5; XC-2 / REVIEW-D-P6-1].

The M0 test-naming convention names AC-mapped tests ``test_ac<N>_*`` so AC
coverage is recomputable mechanically. Phase 6 turns that convention from a
*report* into an *enforced contract*: for every story ``eval<N>`` the set of
acceptance criteria pre-registered in ``docs/design/specs/eval<N>.spec.md`` must
be exactly the set of AC numbers exercised by ``test_ac<N>_*`` tests in that
story's ``tests/test_eval<N>_*.py`` files — no missing AC, no test naming an AC
the spec does not declare, and no two AC tests sharing a function name (which
would silently collapse under name-based coverage tooling).

The guarantee is at **AC-number granularity per story**: a story losing its last
test for an AC, gaining a test for an AC its spec does not declare, or carrying a
test file with no matching spec fails loudly. (It does not pin individual
pre-registered test *names*, so renaming a test while keeping its AC number
covered is allowed.)

This module is a *static* check over the spec and test trees on disk, so it is
independent of which subset of tests pytest happens to be collecting and of the
invoking cwd. ``conftest.py`` calls :func:`check_ac_coverage` at collection and
fails the session loudly on any violation.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# ``- id: "AC-3"`` inside a spec's ``acceptance:`` block. The quoted, dashed,
# numbered form is distinctive enough to match without a full YAML parse (the
# specs carry embedded zero-width spaces that defeat ``yaml.safe_load``), and we
# scope the scan to the acceptance block so an AC id mentioned elsewhere in the
# spec (a cross-reference, a decision note) is not miscounted as declared.
_SPEC_AC_RE = re.compile(r'^\s*-\s*id:\s*"AC-(\d+)"', re.MULTILINE)
_ACCEPTANCE_HEAD_RE = re.compile(r"^acceptance:\s*$", re.MULTILINE)
_TOPLEVEL_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*:")
# a story spec / test file: ``eval<N>`` is the story key.
_SPEC_FILE_RE = re.compile(r"^eval(\d+)\.spec\.md$")
_TEST_FILE_RE = re.compile(r"^test_eval(\d+)_.*\.py$")
# an AC-mapped test function name: ``test_ac<N>_<slug>``.
_TEST_AC_RE = re.compile(r"^test_ac(\d+)_")


def _acceptance_block(text: str) -> str:
    """The lines under a spec's top-level ``acceptance:`` key.

    Empty string if the spec declares no acceptance block. The block runs from
    the ``acceptance:`` line to the next top-level YAML key or a ``---`` fence.
    """
    lines = text.splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if not in_block:
            if _ACCEPTANCE_HEAD_RE.match(line):
                in_block = True
            continue
        if _TOPLEVEL_KEY_RE.match(line) or line.strip() == "---":
            break
        out.append(line)
    return "\n".join(out)


def _spec_acs(specs_dir: Path) -> tuple[dict[str, set[int]], list[str]]:
    """Map each story ``eval<N>`` to the AC numbers its spec declares.

    Returns ``(acs_by_story, malformed)``; ``malformed`` names any spec that has
    an ``acceptance:`` block from which no AC id parsed — a reformatted spec that
    would otherwise silently under-count and enforce nothing, so we fail loud.
    """
    out: dict[str, set[int]] = {}
    malformed: list[str] = []
    for spec in sorted(specs_dir.glob("eval*.spec.md")):
        m = _SPEC_FILE_RE.match(spec.name)
        if not m:
            continue
        story = f"eval{m.group(1)}"
        text = spec.read_text(encoding="utf-8")
        block = _acceptance_block(text)
        acs = {int(n) for n in _SPEC_AC_RE.findall(block)}
        if not acs and _ACCEPTANCE_HEAD_RE.search(text):
            malformed.append(
                f"{spec.name}: has an acceptance block but no AC ids parsed "
                f"(reformatted spec?) — enforcement would be silently vacuous"
            )
        out[story] = acs
    return out, malformed


def _test_ac_defs(tests_dir: Path) -> tuple[dict[str, set[int]], list[str], list[tuple[str, str]]]:
    """Scan test files for ``test_ac<N>_*`` function definitions.

    Returns ``(by_story, orphans, duplicates)`` where ``by_story`` maps a story
    ``eval<N>`` to the AC numbers its files exercise, ``orphans`` are AC tests in
    files that do not belong to a story, and ``duplicates`` are ``(name, where)``
    pairs for any AC test function name that is defined more than once anywhere.
    A file that does not parse (a work-in-progress syntax error) is skipped —
    pytest reports its own collection error, so the AC hook must not crash the
    whole session over it.
    """
    by_story: dict[str, set[int]] = {}
    orphans: list[str] = []
    seen: dict[str, str] = {}          # func name -> first "file:line" seen
    duplicates: list[tuple[str, str]] = []
    for py in sorted(tests_dir.rglob("test_*.py")):
        story_m = _TEST_FILE_RE.match(py.name)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ac_m = _TEST_AC_RE.match(node.name)
            if not ac_m:
                continue
            where = f"{py.name}:{node.lineno}"
            if node.name in seen:
                duplicates.append((node.name, f"{seen[node.name]} and {where}"))
            else:
                seen[node.name] = where
            if story_m:
                by_story.setdefault(f"eval{story_m.group(1)}", set()).add(int(ac_m.group(1)))
            else:
                orphans.append(f"{node.name} ({where})")
    return by_story, orphans, duplicates


def check_ac_coverage(specs_dir: Path, tests_dir: Path) -> list[str]:
    """Return a list of AC-coverage violation messages (empty ⇒ clean).

    Enforced, per story:

    * **missing** — a spec-declared AC with no ``test_ac<N>_*`` test;
    * **misnamed** — a ``test_ac<N>_*`` test naming an AC the story's spec does
      not declare;
    * **spec-less story** — a ``test_eval<N>_*`` file with AC tests but no
      ``eval<N>.spec.md`` to check them against;
    * **malformed spec** — an acceptance block from which no AC id parsed;
    * **orphan** — an AC test in a file outside any ``test_eval<N>_*`` story;
    * **duplicate** — an AC test function name defined more than once.
    """
    specs_dir = Path(specs_dir)
    tests_dir = Path(tests_dir)
    spec_acs, malformed = _spec_acs(specs_dir)
    by_story, orphans, duplicates = _test_ac_defs(tests_dir)

    violations: list[str] = list(malformed)
    for story in sorted(spec_acs, key=lambda s: int(s[4:])):
        expected = spec_acs[story]
        actual = by_story.get(story, set())
        missing = sorted(expected - actual)
        misnamed = sorted(actual - expected)
        if missing:
            violations.append(
                f"{story}: spec declares AC(s) {missing} with no test_ac<N>_* test"
            )
        if misnamed:
            violations.append(
                f"{story}: test_ac tests name AC(s) {misnamed} not declared in "
                f"{story}.spec.md"
            )
    for story in sorted(by_story, key=lambda s: int(s[4:])):
        if story not in spec_acs:
            violations.append(
                f"{story}: has AC tests but there is no {story}.spec.md to enforce "
                f"them against"
            )
    for name, where in duplicates:
        violations.append(f"duplicate AC test name {name!r} defined at {where}")
    for orphan in orphans:
        violations.append(
            f"AC test {orphan} is in a file that maps to no eval<N> story"
        )
    return violations
