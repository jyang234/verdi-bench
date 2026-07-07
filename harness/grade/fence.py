"""The versioned fenced-stdout grader transport [refactor 05 §2].

Extracted from ``grade/container.py`` — the one owner of the fence bytes. The
grader image entrypoint prints exactly ONE fenced JSON document on stdout and
the host scores *nothing* from the agent-writable ``/workspace``: an agent
atexit hook or background thread that rewrites a results file after the grader
wrote it forges nothing [F-H1]. Versioned sentinels: a transport change bumps
the version so old and new images are never silently cross-read.

**FROZEN — grader-image compatibility contract [05 §8]:** the tag strings, the
``VERDI_FENCE_NONCE`` env name, and the emitted output shape are a public seam.
A shipped grader image stamps these exact bytes into its fence; changing any of
them silently cross-reads (or refuses) every existing image. Change only under
an explicit version bump + migration.

This module is the LOWEST grade module: it imports only the standard library, so
the fail-closed error vocabulary (:class:`GradingContainerError` /
:class:`GraderUnavailableError`) lives here too — every heavier layer
(``isolation``, ``runners``, ``plugins/launch``) raises it without importing a
heavier module or forming an import cycle, and the minimal grader-image
entrypoint (``run_holdouts`` → this module) stays free of the docker mechanics.

Contains **no LLM client** — grading's determinism is its authority (the
``grade-has-no-llm-clients`` import contract).

V1→V2 [F-H1 follow-up]: the fence is NONCE-AUTHENTICATED. The host mints a
fresh, unpredictable per-grade nonce, injects it into the container as
``VERDI_FENCE_NONCE``, and accepts only a fence whose marker carries exactly
that nonce. This closes the residual the plain stdout transport left open:
because agent code runs in the SAME container as the grader, it can write to the
captured stdout too, so a well-timed agent could suppress the grader's genuine
fence (e.g. redirect fd 1) and print its own single forged block — and the old
static, publicly-known markers made that block indistinguishable from a real
one. A nonce the agent cannot read defeats that: a forged marker carries the
wrong nonce (or none), so it never matches and the channel reads absent/ambiguous
→ fail-closed. The nonce is only secret if the grader image scrubs
``VERDI_FENCE_NONCE`` from the environment of any agent-executing subprocess
(holdout tests import the solution); that grader-image discipline is the other
half of this defense and is documented in docs/deep-dive.md §2.4. The version
bump forces a clear fail-closed migration: a pre-V2 image emits markers the host
never matches and gets the "rebuild your grader image" refusal below, rather
than a silent cross-read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from ..errors import VerdiRefusal

# The per-grade nonce env name — FROZEN (grader-image compatibility contract):
# the host injects it, the grader image reads it and stamps it into the fence.
NONCE_ENV = "VERDI_FENCE_NONCE"

# Versioned fence tags — FROZEN (grader-image compatibility contract). A holdout
# block can never be mistaken for a plugin block: plugins execute rules over the
# agent-controlled workspace, so their results were equally forgeable in-run
# [F-H1 A.4] and carry a distinct tag.
_HOLDOUT_TAG = "VERDI_HOLDOUT_RESULTS_V2"
_PLUGIN_TAG = "VERDI_PLUGIN_RESULTS_V2"


class GradingContainerError(VerdiRefusal, RuntimeError):
    """The grader ran but failed (nonzero exit, no results) → a **terminal**
    cant_grade(container_failure): re-running won't change the outcome.

    "The grader ran" is a real precondition, not a given: a *down* daemon makes
    ``docker run`` exit 1 without the grader ever running, which would be
    misclassified here as terminal. The pre-flight daemon probe (``preflight``)
    catches that case up front and routes it to the transient
    :class:`GraderUnavailableError` instead [GR-8/GR-11]."""


class GraderUnavailableError(GradingContainerError):
    """The grader could not be run at all — daemon/config/OS error or exit 125 →
    a **transient** cant_grade(grader_unavailable) that a later attempt may
    resolve [GR-11]. Subclass of GradingContainerError so callers that don't care
    still catch it."""


class HoldoutResultsMissingError(GradingContainerError):
    """The no-daemon LocalGradeRunner found no pre-placed ``holdout_results.json``
    in the workspace → a **terminal** cant_grade(holdout_results_missing)
    [ux-friction AC-4].

    A missing grade INPUT on a path with *no container*, NOT a grader that ran
    and failed: the ``--runner local`` case has no container to fail, so it must
    not be misclassified as ``container_failure`` (F7). Terminal because
    re-running without the file cannot change the outcome — ``--retry-terminal``
    is the recovery once the operator places the results. Subclass of
    GradingContainerError so a caller that does not distinguish still fails closed.
    Sibling of :class:`GraderUnavailableError`, so grade_trial must catch it
    before the bare ``GradingContainerError``."""


@dataclass
class HoldoutRun:
    raw_output: dict
    exit_status: int = 0


def _fence_pair(tag: str, nonce: Optional[str]) -> tuple[str, str]:
    """The (begin, end) marker pair for ``tag``, optionally nonce-authenticated.

    With a nonce, the token is bracketed between ``:`` and the trailing dashes
    (``…_BEGIN:<nonce>-----``) so a longer forged guess cannot prefix-match the
    expected marker under ``str.count``. Without a nonce the bare markers are
    used — the local (no-daemon, ADVISORY) path and direct parser unit tests,
    neither of which runs an untrusted container.
    """
    suffix = f":{nonce}" if nonce else ""
    return (f"-----{tag}_BEGIN{suffix}-----", f"-----{tag}_END{suffix}-----")


def holdout_fence(nonce: Optional[str] = None) -> tuple[str, str]:
    """The holdout results (begin, end) markers for a given per-grade nonce."""
    return _fence_pair(_HOLDOUT_TAG, nonce)


def plugin_fence(nonce: Optional[str] = None) -> tuple[str, str]:
    """The plugin results (begin, end) markers for a given per-grade nonce."""
    return _fence_pair(_PLUGIN_TAG, nonce)


# Bare (un-nonced) markers, kept as module constants for the local path and for
# tests that exercise the parser directly.
RESULTS_FENCE_BEGIN, RESULTS_FENCE_END = holdout_fence(None)
PLUGIN_FENCE_BEGIN, PLUGIN_FENCE_END = plugin_fence(None)


def _extract_fence(stdout: str, begin: str, end: str) -> tuple[str, Optional[str]]:
    """One fenced body from container stdout: ``("ok", body)``, or a fail-closed
    status — ``"absent"`` (no fence at all) / ``"ambiguous"`` (more than one
    fence, e.g. agent code printing its own forged block, or inverted markers).
    """
    begins, ends = stdout.count(begin), stdout.count(end)
    if begins == 0 and ends == 0:
        return "absent", None
    if begins != 1 or ends != 1:
        return "ambiguous", None
    start = stdout.index(begin) + len(begin)
    stop = stdout.index(end)
    if stop < start:
        return "ambiguous", None
    return "ok", stdout[start:stop]


def parse_fenced_stdout(
    stdout: str, exit_status: int = 0, *, nonce: Optional[str] = None
) -> HoldoutRun:
    """Extract the grader's fenced holdout results from stdout [F-H1].

    Fail-closed by construction: zero fences → the grader produced no results
    (terminal ``container_failure``, the old missing-file outcome); an
    ambiguous channel or unparseable JSON inside the fence → the malformed
    marker, so the parser flags ``cant_grade(malformed_holdout_output)``. An
    ambiguous channel is never scored.

    ``nonce`` authenticates the fence [F-H1 follow-up]: only a marker carrying
    the per-grade nonce is recognized, so a forged block written by agent code
    that cannot read the nonce reads as absent (wrong/no nonce) rather than
    being scored. ``None`` uses the bare markers (local/ADVISORY path and
    direct parser tests).
    """
    begin, end = holdout_fence(nonce)
    status, body = _extract_fence(stdout, begin, end)
    if status == "absent":
        raise GradingContainerError(
            "grader emitted no fenced holdout results on stdout (expected one "
            f"{RESULTS_FENCE_BEGIN!r} block — a grader image predating the V1 "
            "stdout transport must be rebuilt; see docs/usage-guide.md)"
        )
    if status != "ok":
        return HoldoutRun({"__malformed__": True}, exit_status)
    try:
        raw = json.loads(body or "")
    except json.JSONDecodeError:
        return HoldoutRun({"__malformed__": True}, exit_status)
    return HoldoutRun(raw, exit_status)
