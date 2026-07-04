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


def test_readme_contract_count_matches_importlinter():
    live = len(
        re.findall(r"^\[importlinter:contract:", (_REPO / ".importlinter").read_text(),
                   flags=re.MULTILINE)
    )
    readme = (_REPO / "README.md").read_text()
    m = re.search(r"(\d+)\s+import-linter contracts", readme)
    assert m, "README no longer states an import-linter contract count"
    assert int(m.group(1)) == live, (
        f"README claims {m.group(1)} import-linter contracts but .importlinter "
        f"declares {live}"
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
