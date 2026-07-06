"""Engine registry — the single place engines are named and Harbor is imported.

Keeping engine construction behind :data:`ENGINES` (and the ``harbor`` import inside
its factory, never in the seam, scheduler, budget, or CLI) is what makes "Harbor
confined to the seam" [AC-1] structurally true: callers ask for an engine by name
via :func:`get_engine`; they never import an engine module themselves.

Adding an engine is one :data:`ENGINES` entry [refactor 04 §2]: the ``bench run
--engine`` help (:func:`engine_choices`) and the unknown-engine error both derive
from ``ENGINES.keys()``, and the cross-engine contract suite parametrizes over the
registry — so a new engine is wired and contract-tested by that single line.
"""

from __future__ import annotations

from typing import Callable

from .base import EngineBase


def _fake() -> EngineBase:
    from .fake import FakeEngine

    return FakeEngine()


def _harbor() -> EngineBase:
    from .harbor import HarborEngine

    return HarborEngine()


# name -> zero-arg factory; insertion order is the CLI's rendering order.
ENGINES: dict[str, Callable[[], EngineBase]] = {
    "fake": _fake,
    "harbor": _harbor,
}


def engine_choices() -> str:
    """The registered engine names rendered for ``--engine`` help as ``fake | harbor``."""
    return " | ".join(ENGINES)


def get_engine(name: str) -> EngineBase:
    """Construct the engine registered under ``name`` [AC-1].

    An unknown name fails loudly with the closed choice list derived from the
    registry — never a silent default."""
    try:
        factory = ENGINES[name]
    except KeyError:
        expected = " or ".join(repr(k) for k in ENGINES)
        raise ValueError(f"unknown engine {name!r}; expected {expected}") from None
    return factory()
