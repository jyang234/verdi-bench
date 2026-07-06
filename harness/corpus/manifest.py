"""Corpus manifest builder [refactor 02 §1].

The ``{corpus_id, semver, kind, tasks:[{task_id, sha, status, metadata}]}`` shape
was hand-rolled in three near-identical places (``scripts/shakedown/tripwires.py``,
``scripts/shakedown/official.py``, and the ``full_corpus`` fixture P0 extracted
from ``test_eval6_analyze``). :func:`build_manifest` is the one place that names
that envelope and constructs it *through* the :class:`CorpusManifest` /
:class:`TaskEntry` validators (registry.py owns the shape + invariants; this
module only composes it — single-responsibility).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .registry import CorpusManifest, TaskEntry


def build_manifest(
    *,
    corpus_id: str,
    semver: str,
    kind: str = "public",
    tasks: Iterable[Mapping[str, object]],
) -> CorpusManifest:
    """Assemble a validated :class:`CorpusManifest` from loose task descriptors.

    Each ``tasks`` entry is a mapping carrying at least ``task_id`` and ``sha``,
    plus optional ``status`` and ``metadata`` (any other :class:`TaskEntry` field
    is accepted and validated; an unknown key is refused by ``extra="forbid"``).

    ``status`` defaults to ``"admitted"`` — the schedulable state a ready-made
    corpus wants, and what all three former hand-writers set — overriding
    ``TaskEntry``'s conservative ``"pending-curation"`` default. Pass ``status``
    explicitly for a pending/quarantined entry.

    Returns the model (not JSON): model consumers use it directly; JSON callers
    take ``.to_json()``.
    """
    entries = [TaskEntry(**{"status": "admitted", **dict(t)}) for t in tasks]
    return CorpusManifest(corpus_id=corpus_id, semver=semver, kind=kind, tasks=entries)
