"""Shared CLI plumbing for the stage verbs [refactor 02 §3].

Each stage CLI is a thin shell over its subsystem ``api`` module: it parses
arguments, resolves the ledgered actor, maps typed refusals to an exit code, and
echoes. This module owns the two idioms every verb repeated — the
refusal→``typer.Exit`` ceremony and actor→``EventContext`` resolution [GR-12] —
so a stage CLI adds a verb without re-deriving either, and a refusal type a verb
forgot to enumerate surfaces loudly rather than as a raw traceback.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import typer

from .ledger.actor import ActorResolutionError, resolve_actor
from .ledger.events import EventContext


@contextmanager
def refusal_exit(*errors: type[BaseException], code: int = 2) -> Iterator[None]:
    """Map an enumerated typed refusal to ``typer.echo(str(err), err=True)`` +
    ``typer.Exit(code)`` — the try/except block every verb repeated.

    The guarded refusal types are named explicitly at the call site, so this is
    byte-identical to the hand-written blocks (same types caught, same stderr
    text, same exit code); it only removes the duplication. ``code`` defaults to
    2 (the pre-registration/refusal convention); the few exit-1 sites pass it.
    """
    try:
        yield
    except errors as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=code)


def resolve_actor_or_exit(flag_value: str | None) -> str:
    """Resolve the ledgered actor or exit 2 with the named refusal [GR-12].

    The single home for the five identical ``_resolve_actor_or_exit`` copies
    (corpus/forensics/review/contamination/process CLIs)."""
    with refusal_exit(ActorResolutionError):
        return resolve_actor(flag_value)


def event_context(experiment_dir: Path | str, actor_flag: str | None) -> EventContext:
    """Build the ``EventContext(experiment_id=<dir>.name, actor=<resolved>)``
    every ledgering verb constructs [GR-12].

    Resolves the actor (exiting 2 on an unresolvable actor) and stamps the
    experiment id from the directory name — one ledger, one experiment id,
    exactly as run/grade/plan do today."""
    return EventContext(
        experiment_id=Path(experiment_dir).name,
        actor=resolve_actor_or_exit(actor_flag),
    )
