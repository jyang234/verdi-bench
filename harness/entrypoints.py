"""Stage-entrypoint registry [EVAL-3 §M7, XC-3].

Each stage registers a callable that performs exactly one ledgered operation.
``test_ac7_one_event_per_operation`` sweeps this registry and asserts every
invocation appends exactly one event (success or fail-closed). Later stories
register their verbs here so the property covers them automatically — the sweep
now asserts an explicit expected set, so a stage that forgets to register fails
the test closed rather than silently escaping the property.

An entrypoint is ``(name, fn, prepare)`` where ``fn(ctx_dir) -> None`` runs one
operation against a prepared fixture directory (containing at least
``experiment.yaml`` and ``ledger.ndjson``) and appends **exactly one** event.
``prepare(ctx_dir)`` optionally seeds ledger preconditions the operation needs
(e.g. a judge verdict a human verdict presupposes); its events are set up before
the sweep snapshots the count, so only ``fn``'s single event is measured. The
registry stays import-light: stage modules call :func:`register_entrypoint` at
import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

EntrypointFn = Callable[[str], None]


@dataclass(frozen=True)
class Entrypoint:
    name: str
    fn: EntrypointFn
    prepare: Optional[EntrypointFn] = None


_REGISTRY: dict[str, Entrypoint] = {}


def register_entrypoint(
    name: str, fn: EntrypointFn, *, prepare: Optional[EntrypointFn] = None
) -> None:
    _REGISTRY[name] = Entrypoint(name, fn, prepare)


def all_entrypoints() -> list[Entrypoint]:
    return list(_REGISTRY.values())
