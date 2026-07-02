"""Fake provider for tests — scripts completions and faults deterministically."""

from __future__ import annotations

from typing import Callable, Union

from .base import Provider


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
