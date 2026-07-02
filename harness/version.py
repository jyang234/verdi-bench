"""Instrument identity — semver + git sha, stamped into every ledger event.

[EVAL-1 invariant: provenance on every artifact]. This is the single source of
truth other modules read; event constructors call :func:`instrument_identity`.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import TypedDict


class InstrumentIdentity(TypedDict):
    version: str
    git_sha: str


def semver() -> str:
    """The instrument's package version (pyproject ``version``)."""
    try:
        return metadata.version("verdi-bench")
    except metadata.PackageNotFoundError:  # pragma: no cover - editable-install edge
        return "0.0.0+unknown"


@lru_cache(maxsize=1)
def git_sha() -> str:
    """Best-effort git sha of the instrument checkout.

    Never raises: a detached/absent git tree yields ``"unknown"`` rather than
    letting provenance stamping fail. The sha describes *this instrument*, not
    any trial subject.
    """
    repo_root = Path(__file__).resolve().parent.parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else "unknown"


def instrument_identity() -> InstrumentIdentity:
    return {"version": semver(), "git_sha": git_sha()}
