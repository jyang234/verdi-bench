"""Actor provenance resolution [GR-12, D-P7-7].

Every ledgering verb stamps an ``actor`` on its :class:`EventContext`. The old
sites swallowed a failed ``getpass.getuser()`` into the literal ``"unknown"`` —
fail-*quiet* provenance that records a lie. ``resolve_actor`` centralizes the
one policy: an explicit ``--actor`` wins; else the OS user; else a loud refusal
that names the flag. It never returns ``"unknown"``.

This is fail-loud *provenance*, not authentication — a headless environment
passes ``--actor``. (Approver identity for corpus admission is security-relevant
and handled separately under D-P7-3.)
"""

from __future__ import annotations

import getpass

from ..errors import VerdiRefusal


class ActorResolutionError(VerdiRefusal, RuntimeError):
    """Neither ``--actor`` nor the OS user could name the actor — refuse rather
    than ledger a ``"unknown"`` that masks who acted [GR-12]."""


def resolve_actor(flag_value: str | None) -> str:
    """Resolve the ledgered actor: explicit flag wins, else the OS user.

    ``getpass.getuser`` itself consults ``LOGNAME``/``USER``/``LNAME``/
    ``USERNAME`` then the password database; only if *all* of those fail (the
    ``OSError``/``KeyError`` it raises) do we refuse — naming ``--actor`` as the
    fix — instead of recording ``"unknown"``.
    """
    if flag_value:
        return flag_value
    try:
        return getpass.getuser()
    except (OSError, KeyError) as e:
        raise ActorResolutionError(
            "could not resolve the acting user from the environment "
            f"({e!r}); pass --actor <name> to name who is performing this "
            "operation (provenance is never recorded as 'unknown') [GR-12]"
        ) from e
