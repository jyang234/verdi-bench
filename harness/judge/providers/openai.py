"""OpenAI provider client (pinned via the fully-versioned model id)."""

from __future__ import annotations

from .base import Provider, ProviderError
from ._http import post_json, require_key


def _content(resp: dict) -> str:
    """Extract the completion text, failing closed on an error-shaped 200 [JD-3].

    An error/unexpected body must raise ``ProviderError`` (→ provider_error) here
    rather than a bare ``KeyError``/``IndexError`` that escapes the judge client
    with no verdict event.
    """
    if "error" in resp:
        raise ProviderError(f"openai error response: {resp['error']}")
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"unexpected openai response shape: {resp}") from e


class OpenAIProvider(Provider):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        model = model_id.split("/", 1)[1]
        body = {"model": model, "temperature": temperature, "messages": messages}
        resp = post_json(
            "https://api.openai.com/v1/chat/completions",
            body,
            {"authorization": f"Bearer {require_key('OPENAI_API_KEY')}"},
        )
        return _content(resp)
