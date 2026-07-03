"""The 3.12 compatibility gate mechanism [XC-6 / REVIEW-D-7].

CI runs ``compileall`` under a real 3.12 interpreter; that job is the actual gate.
Here we prove the gate *command* does its job — the current tree compiles, and a
planted syntax error makes ``compileall`` exit nonzero — using the running
interpreter (the mechanism is interpreter-agnostic; only the 3.12 job pins the
version). This keeps "3.12-compatible" from being an unverified claim.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _compileall(*targets: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "compileall", "-q", *targets],
        cwd=_REPO, capture_output=True, text=True, timeout=120,
    )


def test_repo_tree_compiles():
    """The whole shipped tree parses/compiles — the gate passes on a clean tree."""
    assert _compileall("harness", "conftest.py").returncode == 0


def test_gate_catches_a_syntax_error(tmp_path):
    """A planted syntax error makes the gate fail (reproduce-first)."""
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n    pass\n", encoding="utf-8")
    result = _compileall(str(bad))
    assert result.returncode != 0
