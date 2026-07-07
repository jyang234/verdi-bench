"""Provider-key presence gating [refactor 02 §2, 08 §1].

The opt-in real-fidelity layers (official.py, harbor.py) each hand-rolled the
same ``if not os.environ.get("…KEY"): raise …`` check. :func:`require_env_keys`
is the one small helper they share: it fails loudly and *names every* missing
key (not just the first), so a two-key run isn't a guess-and-retry.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional


class MissingEnvKeysError(RuntimeError):
    """One or more required environment keys were unset or empty."""

    def __init__(self, required: tuple[str, ...], missing: list[str]) -> None:
        self.required = required
        self.missing = missing
        super().__init__(
            f"missing required environment key(s): {', '.join(missing)} — set "
            "them before running (e.g. `uv run --env-file .env python …`)"
        )


def require_env_keys(
    *names: str, env: Optional[Mapping[str, str]] = None
) -> dict[str, str]:
    """Return ``{name: value}`` for every required key, or raise loudly.

    A key that is unset OR present-but-empty counts as missing (an empty API key
    would fail opaquely deep in a provider call — refuse up front instead). Reads
    ``os.environ`` unless an explicit ``env`` mapping is supplied (testable).
    """
    source = os.environ if env is None else env
    missing = [n for n in names if not source.get(n)]
    if missing:
        raise MissingEnvKeysError(names, missing)
    return {n: source[n] for n in names}
