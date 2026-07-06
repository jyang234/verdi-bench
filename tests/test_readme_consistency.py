"""README claims that are mechanically checkable stay true [XC-7].

Phase 6's ethos is that a claim about the instrument is backed by an enforcing
check. The import-contract count is a low-churn, load-bearing claim, so pin it:
the README's "N import-linter contracts" must match the number of contracts
actually declared in ``.importlinter``.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


_NUMBER_WORDS = {
    3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
    9: "nine", 10: "ten",
}


def _live_contract_count() -> int:
    return len(
        re.findall(r"^\[importlinter:contract:", (_REPO / ".importlinter").read_text(),
                   flags=re.MULTILINE)
    )


def test_readme_contract_count_matches_importlinter():
    live = _live_contract_count()
    readme = (_REPO / "README.md").read_text()
    m = re.search(r"(\d+)\s+import-linter contracts", readme)
    assert m, "README no longer states an import-linter contract count"
    assert int(m.group(1)) == live, (
        f"README claims {m.group(1)} import-linter contracts but .importlinter "
        f"declares {live}"
    )


def test_deep_dive_contract_count_matches_importlinter():
    """PRA-M docs: the deep dive's spelled-out contract total must not drift from
    the number of contracts actually declared (it said 'five' when seven were
    kept). The trust-architecture table splits the set as 'three of the N' plus
    'M structural contracts complete the set'; pin that N."""
    live = _live_contract_count()
    deep = (_REPO / "docs" / "deep-dive.md").read_text()
    word = _NUMBER_WORDS[live]
    assert re.search(rf"of the {word} import-linter contracts", deep), (
        f".importlinter declares {live} contracts but docs/deep-dive.md does not "
        f"say 'of the {word} import-linter contracts' — stale contract count"
    )


# --- XC-7: the README Usage block documents exactly the registered verbs -----
def _registered_verbs(app) -> set[str]:
    """Every ``<group> <command>`` (or bare ``<command>``) path in the app."""
    import typer

    cli = typer.main.get_command(app)

    def walk(cmd, prefix=""):
        out: list[str] = []
        for name, sub in getattr(cmd, "commands", {}).items():
            path = f"{prefix}{name}"
            if getattr(sub, "commands", {}):
                out += walk(sub, path + " ")
            else:
                out.append(path)
        return out

    return set(walk(cli))


def _documented_verbs(readme: str, groups: set[str]) -> set[str]:
    """Verb paths named after ``bench`` in the README Usage block. A leading
    command-group token (e.g. ``corpus``) claims two tokens; otherwise one."""
    docs: set[str] = set()
    for line in readme.splitlines():
        if "bench " not in line:
            continue
        after = line.split("bench ", 1)[1].split()
        tokens: list[str] = []
        for raw in after:
            t = raw.strip("`.,")  # inline-prose backticks / trailing punctuation
            if t and t[0].isalpha() and not t.startswith("<"):
                tokens.append(t)
            else:
                break
        if not tokens:
            continue
        if tokens[0] in groups and len(tokens) >= 2:
            docs.add(f"{tokens[0]} {tokens[1]}")
        else:
            docs.add(tokens[0])
    return docs


def _readme_verb_diff(registered: set[str], readme: str):
    groups = {p.split()[0] for p in registered if " " in p}
    documented = _documented_verbs(readme, groups)
    undocumented = registered - documented  # registered but not in the README
    nonexistent = documented - registered   # in the README but not registered
    return undocumented, nonexistent


def test_readme_usage_documents_every_registered_verb():
    from harness.cli import app

    registered = _registered_verbs(app)
    readme = (_REPO / "README.md").read_text()
    undocumented, nonexistent = _readme_verb_diff(registered, readme)
    assert not undocumented, f"registered verbs missing from README Usage: {sorted(undocumented)}"
    assert not nonexistent, f"README Usage names verbs that do not exist: {sorted(nonexistent)}"


def test_readme_checker_flags_a_planted_undocumented_verb():
    """The reproduce-first artifact: a registered verb absent from the README is
    caught. Plant a phantom verb into the registered set and assert the checker
    flags it as undocumented."""
    from harness.cli import app

    registered = _registered_verbs(app) | {"corpus phantom"}
    readme = (_REPO / "README.md").read_text()
    undocumented, _ = _readme_verb_diff(registered, readme)
    assert "corpus phantom" in undocumented
