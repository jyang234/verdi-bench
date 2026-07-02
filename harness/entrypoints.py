"""Stage-entrypoint registry [EVAL-3 §M7].

Each stage registers a callable that performs exactly one ledgered operation.
``test_ac7_one_event_per_operation`` sweeps this registry and asserts every
invocation appends exactly one event (success or fail-closed). Later stories
register their verbs here so the property covers them automatically.

An entrypoint is ``(name, fn)`` where ``fn(ctx_dir) -> None`` runs one operation
against a prepared fixture directory (containing at least ``experiment.yaml`` and
``ledger.ndjson``). The registry stays import-light: stage modules call
:func:`register_entrypoint` at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

EntrypointFn = Callable[[str], None]


@dataclass(frozen=True)
class Entrypoint:
    name: str
    fn: EntrypointFn


_REGISTRY: dict[str, Entrypoint] = {}


def register_entrypoint(name: str, fn: EntrypointFn) -> None:
    _REGISTRY[name] = Entrypoint(name, fn)


def all_entrypoints() -> list[Entrypoint]:
    return list(_REGISTRY.values())
