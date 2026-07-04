"""Solution-overlap detector: winnowing fingerprints [EVAL-10 AC-4, D003].

Deterministic fingerprint overlap between what an agent produced and what it
could not have independently produced — the task's oracle solution and the
holdout content. Winnowing (Schleimer/Wilkerson/Aiken) over lowercased token
k-grams is robust to the whitespace/case cosmetics that defeat raw text
comparison. Hashes come from :mod:`hashlib`, never Python's salted ``hash()``,
so output is byte-identical for fixed inputs across processes.

Any holdout overlap at/above threshold additionally raises the EVAL-4
insulation alarm channel (:class:`harness.run.seam.HoldoutLeakError`) — the
agent should never have seen that content at all [EVAL-4 AC-9].

The k/window constants are part of the detector's identity: changing them
re-scores history, so treat them like the threshold — versioned, never tuned
against observed trials.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from ..run.seam import HoldoutLeakError

#: Flag at/above this containment when the experiment pre-registers no
#: ``contamination.overlap_threshold`` — a fixed constant, not a tunable.
DEFAULT_OVERLAP_THRESHOLD = 0.5
_K = 5        # tokens per shingle
_WINDOW = 4   # winnowing window (guarantees matches ≥ _WINDOW+_K-1 tokens hit)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class OverlapError(ValueError):
    """A reference that cannot be fingerprinted — refused loudly, never scored
    0.0 (a silent zero would mask a broken comparison as a clean trial)."""


@dataclass(frozen=True)
class OverlapResult:
    """Deterministic outcome of one trial-vs-references comparison [AC-4]."""

    oracle_score: Optional[float]
    holdout_scores: tuple[float, ...]
    threshold: float
    flagged: bool


def _fingerprints(text: str) -> frozenset[int]:
    """Winnowed fingerprint set of ``text``.

    Lowercased word tokens → k-gram sha256 prefixes → per-window minimum
    (rightmost on ties, per the winnowing paper). Fewer hashes than a window
    keeps them all; fewer tokens than a shingle yields the honest empty set.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < _K:
        return frozenset()
    hashes = [
        int.from_bytes(
            hashlib.sha256(" ".join(tokens[i : i + _K]).encode("utf-8")).digest()[:8],
            "big",
        )
        for i in range(len(tokens) - _K + 1)
    ]
    if len(hashes) <= _WINDOW:
        return frozenset(hashes)
    # positions are not recorded, so the classic tie-breaking rule is moot:
    # each window contributes its minimum hash value to the set.
    selected = {
        min(hashes[start : start + _WINDOW])
        for start in range(len(hashes) - _WINDOW + 1)
    }
    return frozenset(selected)


def _containment(reference_fp: frozenset[int], solution_fp: frozenset[int]) -> float:
    """Fraction of the reference's fingerprints present in the solution."""
    return len(reference_fp & solution_fp) / len(reference_fp)


def solution_overlap(
    solution: str,
    *,
    oracle: Optional[str] = None,
    holdouts: Sequence[str] = (),
    threshold: Optional[float] = None,
) -> OverlapResult:
    """Compare a trial's solution against the oracle and holdout content [AC-4].

    The score is containment: the fraction of a *reference's* winnowed
    fingerprints found in the solution (a solution may contain much more than
    the copied part; symmetric similarity would dilute that away). At/above
    ``threshold`` the trial is flagged; a holdout at/above threshold raises
    :class:`HoldoutLeakError` (with the :class:`OverlapResult` attached as
    ``.result``) — holdout content in a solution is an insulation breach, not
    merely contamination. A reference too short to fingerprint raises
    :class:`OverlapError` rather than silently scoring 0.0.
    """
    if threshold is None:
        threshold = DEFAULT_OVERLAP_THRESHOLD
    if not 0 < threshold <= 1:
        raise OverlapError(f"threshold {threshold!r} is not in (0, 1]")
    solution_fp = _fingerprints(solution)

    oracle_score: Optional[float] = None
    if oracle is not None:
        oracle_fp = _fingerprints(oracle)
        if not oracle_fp:
            raise OverlapError(
                f"oracle solution is too short to fingerprint (< {_K} tokens); "
                "cannot measure overlap against it"
            )
        oracle_score = _containment(oracle_fp, solution_fp)

    holdout_scores: list[float] = []
    for i, holdout in enumerate(holdouts):
        holdout_fp = _fingerprints(holdout)
        if not holdout_fp:
            raise OverlapError(
                f"holdout #{i} is too short to fingerprint (< {_K} tokens); "
                "cannot measure overlap against it"
            )
        holdout_scores.append(_containment(holdout_fp, solution_fp))

    flagged = (oracle_score is not None and oracle_score >= threshold) or any(
        s >= threshold for s in holdout_scores
    )
    result = OverlapResult(
        oracle_score=oracle_score,
        holdout_scores=tuple(holdout_scores),
        threshold=threshold,
        flagged=flagged,
    )
    leaking = [i for i, s in enumerate(holdout_scores) if s >= threshold]
    if leaking:
        err = HoldoutLeakError(
            f"trial solution overlaps holdout content #{leaking[0]} "
            f"(score={holdout_scores[leaking[0]]:.3f} >= {threshold}); the agent "
            "should never have seen holdout content at all "
            "[EVAL-4 AC-9 / EVAL-10 AC-4]"
        )
        err.result = result
        raise err
    return result
