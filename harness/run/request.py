"""The typed, versioned trial request file [refactor 03 §4, A1].

``/verdi/request.json`` is the harness↔image contract: the harness writes it, the
trial image reads it. It had no schema at all — the payload was a bare dict built
inline in the engine. :class:`TrialRequestFile` promotes it to a public pydantic
model with a ``schema_version``.

A1 (pre-approved, ``decisions.ndjson``): the version is an **additive** key.
Existing images ``json.loads`` the file and pick ``prompt`` / ``arm`` / ``model`` /
``payload``, so an added ``schema_version`` breaks nothing; a future consumer can
branch on it. ``verdi_agent.read_request()`` is the reference consumer and
tolerates the field's absence (a pre-A1 engine wrote the same keys without it) —
the migration story is "additive field, verify covers both".

This is a versioned public seam: changing the field set or its meaning requires a
``schema_version`` bump with a compatibility story (CLAUDE.md contract rules).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# The current request-file schema version. Bump only with a compatibility story.
TRIAL_REQUEST_SCHEMA_VERSION = 1


class TrialRequestFile(BaseModel):
    """The ``/verdi/request.json`` payload the engine writes and the image reads.

    ``extra="forbid"``: the harness OWNS this file, so an unknown key is a
    write-side bug, not silent data. The trial image reads it read-only, from
    OUTSIDE ``/workspace``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = TRIAL_REQUEST_SCHEMA_VERSION
    prompt: str
    arm: str
    model: str
    payload: dict = {}
