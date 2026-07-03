"""Fake provider for tests — scripts completions and faults deterministically.

Also hosts the *deterministic* no-network judge (:class:`DeterministicFakeJudge`)
selected by a ``fake/`` judge-model prefix — the judge analog of the fake run
engine, so a complete fake-engine experiment can run ``bench judge`` end-to-end
without any provider network call. It judges by holdout pass counts, so it is
content-based (order-consistent), not position-biased.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Union

from .base import Provider

_HOLDOUT_RE = re.compile(r"## Holdout results\n(.*)")


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


class DeterministicFakeJudge(Provider):
    """No-network content-based judge for the fake path [analogous to the fake
    run engine]. Makes no external call — every completion is a pure function of
    the rendered packet."""

    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        return deterministic_verdict(messages)


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
        item = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return item
