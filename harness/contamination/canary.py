"""Contamination canaries: deterministic derivation + inert embedding [EVAL-10 AC-2].

A model that completes the canary *without it in context* has seen the task in
training — near-zero false-positive proof of membership. The evidentiary value
dies if a canary value leaks through any published surface, so values are
hash-only everywhere outside the task content itself; renders and packets are
guarded by the shared scrub mechanism — the ``VBCANARY-`` marker format is a
built-in pattern of :func:`harness.blind.core.identity_pattern_list`, so every
scrub/assert surface (judge packets, review packets) kills it. This is a
*different* canary corpus from the blinding canaries (§7.4) — separate
namespace, separate purpose, one shared scrub. Deterministic by construction:
a namespaced sub-hash of ``task_sha``, no randomness [§7.5].
"""

from __future__ import annotations

import hashlib

# Versioned derivation namespace: bumping it re-keys every canary, so it is a
# contract — a silent change would orphan every embedded marker.
_NAMESPACE = "verdi-bench/contamination-canary/v1"
_PREFIX = "VBCANARY-"


class CanaryError(ValueError):
    """A canary derivation/embedding precondition failed [fail-loudly]."""


def derive_canary(task_sha: str) -> str:
    """The task's canary token — a namespaced sub-hash of ``task_sha`` [AC-2].

    Reproducible from the manifest alone (no randomness, no state), so the
    probe can re-derive it without ever storing the value. Sha *format* is the
    registry's concern; here only an empty identity is unusable.
    """
    if not isinstance(task_sha, str) or not task_sha:
        raise CanaryError(
            f"task_sha {task_sha!r} is empty; cannot derive a canary from a "
            "missing task identity"
        )
    digest = hashlib.sha256(f"{_NAMESPACE}:{task_sha}".encode("utf-8")).hexdigest()
    return _PREFIX + digest[:32]


def hash_canary(canary: str) -> str:
    """sha256 of the canary value — the only form events and manifests carry."""
    return hashlib.sha256(canary.encode("utf-8")).hexdigest()


def embed_canary(content: dict, canary: str) -> dict:
    """A copy of task ``content`` with the canary embedded as an inert marker.

    The marker is an HTML-style comment appended to the prompt — visible to a
    training pipeline that ingests the task, semantically inert for an agent
    solving it. Pure: the input dict is not mutated. Re-embedding raises — a
    double marker means two code paths both think they own admission.
    """
    prompt = content.get("prompt")
    if not isinstance(prompt, str):
        raise CanaryError(
            "task content has no string 'prompt' to embed a canary into; "
            f"got keys {sorted(content)}"
        )
    if canary in prompt:
        raise CanaryError(
            "canary is already embedded in this task's prompt; refusing a "
            "double embed"
        )
    embedded = dict(content)
    embedded["prompt"] = f"{prompt}{_marker(canary)}"
    return embedded


def _marker(canary: str) -> str:
    return f"\n\n<!-- {canary} -->\n"


def strip_canary(text: str, canary: str) -> str:
    """``text`` with the task's embedded canary marker removed.

    The exact inverse of :func:`embed_canary` for probe use: the probe must
    send the task content *without* its canary (a canary in context proves
    nothing) [AC-3]. Removes the marker form wherever it appears; a canary
    value surviving outside the marker form is NOT removed — that anomaly is
    the probe's fail-closed ``canary_in_prompt`` refusal, not a scrub.
    """
    return text.replace(_marker(canary), "").replace(f"<!-- {canary} -->", "")
