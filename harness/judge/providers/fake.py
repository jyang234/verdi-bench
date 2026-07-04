"""Fake provider for tests — scripts completions and faults deterministically.

Also hosts the *deterministic* no-network provider
(:class:`DeterministicFakeProvider`) selected by a ``fake/`` model prefix — the
provider analog of the fake run engine, so a complete fake-engine experiment can
run ``bench judge`` **and** ``bench process score`` end-to-end without any
provider network call. It inspects the packet's system prompt to serve either a
judge verdict (by holdout pass counts, so content-based and order-consistent) or
per-dimension process scores (deterministic in the transcript + dimension).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Callable, Union

from .base import Provider

# Holdout results are fenced with a content-derived delimiter [JD-8]; read the
# JSON between the open/close fence markers, not the fence line itself.
_HOLDOUT_RE = re.compile(r"## Holdout results\n<<[0-9a-f]+>>\n(.*)\n<<[0-9a-f]+>>")
# a rubric dimension renders as "## <name> (<dim_id>), scale 1..<n>"
_DIM_RE = re.compile(r"\(([a-z_][a-z0-9_]*)\), scale 1\.\.")
_PROCESS_SYSTEM_MARKER = "how the work was done"
_FORENSIC_SYSTEM_MARKER = "how a work trajectory unfolded"
# forensic packets instruct: {"suspicions": {"<id>": <true|false>, ...}, ...}
_FORENSIC_DETECTOR_RE = re.compile(r'"([a-z_]+)": <true\|false>')
_SCALE_MIN, _SCALE_MAX = 1, 5


def _passing_holdouts(block: str) -> int:
    try:
        items = json.loads(block)
    except (ValueError, TypeError):
        return 0
    return sum(
        1 for it in items if isinstance(it, dict) and it.get("result") == "pass"
    )


def deterministic_verdict(messages: list[dict]) -> str:
    """A content-based judge: the response with more passing holdouts wins.

    Deterministic and order-consistent (it reads content, not position), so
    ``judge_pair``'s two orders agree and the verdict is not downgraded to a
    position-bias TIE unless the responses genuinely tie on holdouts.
    """
    body = messages[-1]["content"]
    blocks = _HOLDOUT_RE.findall(body)
    p1 = _passing_holdouts(blocks[0]) if len(blocks) > 0 else 0
    p2 = _passing_holdouts(blocks[1]) if len(blocks) > 1 else 0
    if p1 == p2:
        return json.dumps(
            {"winner": "TIE", "reason": "equal holdout pass counts", "evidence": []}
        )
    winner = "1" if p1 > p2 else "2"
    return json.dumps(
        {
            "winner": winner,
            "reason": f"response {winner} passed more holdouts",
            "evidence": [
                {"kind": "holdout", "response": int(winner), "ref": f"holdouts::{winner}"}
            ],
        }
    )


def deterministic_process_scores(messages: list[dict]) -> str:
    """Per-dimension process scores, deterministic in the transcript + dimension.

    Parses the dimension ids from the rendered rubric and assigns each a stable
    1..5 score derived from the packet content, so different transcripts yield
    different (but reproducible) scores — enough variance for the analyze
    correlation/kappa tables without any network call.
    """
    body = messages[-1]["content"]
    dims = _DIM_RE.findall(body)
    scores: dict[str, int] = {}
    for dim in dims:
        h = hashlib.sha256(f"{body}||{dim}".encode("utf-8")).digest()
        scores[dim] = _SCALE_MIN + int.from_bytes(h[:4], "big") % (_SCALE_MAX - _SCALE_MIN + 1)
    return json.dumps({"scores": scores})


def deterministic_forensic_review(messages: list[dict]) -> str:
    """A forensic review deterministic in the packet content: each detector's
    suspicion bit is a stable hash of transcript + detector id, so fixture
    experiments produce reproducible calibration tables without a network."""
    body = messages[-1]["content"]
    suspicions: dict[str, bool] = {}
    for detector in _FORENSIC_DETECTOR_RE.findall(body):
        h = hashlib.sha256(f"{body}||{detector}".encode("utf-8")).digest()
        suspicions[detector] = bool(h[0] % 2)
    return json.dumps(
        {
            "suspicions": suspicions,
            "narrative": "deterministic fake review derived from packet content",
        }
    )


class DeterministicFakeProvider(Provider):
    """No-network deterministic provider for the fake path [analogous to the fake
    run engine]. Serves a judge verdict, process scores, or a forensic review
    depending on the packet's system prompt; every completion is a pure function
    of the rendered packet, so no external call is ever made."""

    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        system = messages[0]["content"] if messages else ""
        if _PROCESS_SYSTEM_MARKER in system:
            return deterministic_process_scores(messages)
        if _FORENSIC_SYSTEM_MARKER in system:
            return deterministic_forensic_review(messages)
        return deterministic_verdict(messages)


class FakeProviderExhausted(RuntimeError):
    """A scripted :class:`FakeProvider` was called more times than it has
    responses. Raised instead of silently replaying the last item [RN-18] — a
    silent replay can hide a miscounted or under-scripted test."""


class FakeProvider(Provider):
    def __init__(self, responses: Union[list, Callable[[list[dict]], str]]):
        """``responses`` is either a list consumed per call (each item a str to
        return or an Exception to raise) or a callable ``messages -> str``."""
        self._responses = responses
        self._i = 0
        self.calls: list[dict] = []

    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        self.calls.append({"model_id": model_id, "messages": messages, "temperature": temperature})
        if callable(self._responses):
            return self._responses(messages)
        if self._i >= len(self._responses):
            raise FakeProviderExhausted(
                f"FakeProvider exhausted: {len(self._responses)} scripted "
                f"response(s), but call #{self._i + 1} was requested"
            )
        item = self._responses[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return item
