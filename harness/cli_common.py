"""Shared CLI plumbing for the stage verbs [refactor 02 §3].

Each stage CLI is a thin shell over its subsystem ``api`` module: it parses
arguments, resolves the ledgered actor, maps typed refusals to an exit code, and
echoes. This module owns the two idioms every verb repeated — the
refusal→``typer.Exit`` ceremony and actor→``EventContext`` resolution [GR-12] —
so a stage CLI adds a verb without re-deriving either. ``refusal_exit()`` with no
arguments catches the shared :class:`~harness.errors.VerdiRefusal` base
uniformly, so a refusal type a verb forgot to enumerate is still a clean named
exit 2 rather than a raw traceback [refactor 13 OI-B]. A verb that maps DIFFERENT
refusals to DIFFERENT exit codes or messages keeps an explicit narrow
enumeration: it catches exactly the named types and lets everything else
propagate to its sibling handler (the grade code-1/code-2 ladder, run's
NoTasksError→BadParameter, anchor's CHAIN BROKEN).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import typer

from .errors import VerdiRefusal
from .ledger.actor import ActorResolutionError, resolve_actor
from .ledger.events import EventContext
from .ledger.identity import ExperimentIdResolutionError, derive_experiment_id


@contextmanager
def refusal_exit(*errors: type[BaseException], code: int = 2) -> Iterator[None]:
    """Map a refusal to ``typer.echo(str(err), err=True)`` + ``typer.Exit(code)``
    — the try/except block every verb repeated.

    Called with no ``errors`` it catches the shared ``VerdiRefusal`` base: every
    stated-reason refusal maps uniformly to ``code`` (default 2, the
    pre-registration/refusal convention), so a type a verb forgot to enumerate is
    a clean exit rather than a traceback [refactor 13 OI-B]. Called with explicit
    types it catches EXACTLY those — the byte-identical hand-written block, kept
    where a verb maps different refusals to different codes/messages and must let
    the un-named ones propagate to a sibling handler. ``code`` is honored either
    way; the few exit-1 sites pass it.
    """
    caught: tuple[type[BaseException], ...] = errors or (VerdiRefusal,)
    try:
        yield
    except caught as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=code)


def resolve_actor_or_exit(flag_value: str | None) -> str:
    """Resolve the ledgered actor or exit 2 with the named refusal [GR-12].

    The single home for the five identical ``_resolve_actor_or_exit`` copies
    (corpus/forensics/review/contamination/process CLIs)."""
    with refusal_exit(ActorResolutionError):
        return resolve_actor(flag_value)


def event_context(experiment_dir: Path | str, actor_flag: str | None) -> EventContext:
    """Build the ``EventContext`` the remaining ledgering verbs construct [GR-12].

    Resolves the actor (exiting 2 on an unresolvable actor) and derives the
    experiment id through the one shared seam every stage uses
    (:func:`~harness.ledger.identity.derive_experiment_id`) — the RESOLVED
    directory name, so `bench <verb> .` stamps the experiment's real name rather
    than the empty '' its unresolved ``.name`` used to bake into the chain
    [ux-friction AC-1]. A path that resolves to a nameless directory refuses with
    a clean exit 2 (like the actor refusal) rather than ever ledgering an empty
    id."""
    with refusal_exit(ExperimentIdResolutionError):
        experiment_id = derive_experiment_id(experiment_dir)
    return EventContext(
        experiment_id=experiment_id,
        actor=resolve_actor_or_exit(actor_flag),
    )
