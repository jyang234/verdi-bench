"""Engine factory — the single place Harbor is imported.

Keeping the ``harbor`` import inside ``harness.run.engines`` (never in the seam,
scheduler, budget, or CLI) is what makes "Harbor confined to the seam" [AC-1]
structurally true. Callers ask for an engine by name; they never name Harbor.
"""

from __future__ import annotations

from ..types import Engine


def get_engine(name: str, **kwargs) -> Engine:
    if name == "fake":
        from .fake import FakeEngine

        return FakeEngine()
    if name == "harbor":
        from .harbor import HarborEngine

        return HarborEngine(**kwargs)
    raise ValueError(f"unknown engine {name!r}; expected 'fake' or 'harbor'")
