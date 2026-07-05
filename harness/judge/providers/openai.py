"""OpenAI provider client (pinned via the fully-versioned model id)."""

from __future__ import annotations

from .base import MAX_OUTPUT_TOKENS, Provider, ProviderContextOverflow, ProviderError, normalize_usage
from ._http import post_json, require_key


def _content(resp: dict) -> str:
    """Extract the completion text, failing closed on an error-shaped 200 [JD-3].

    An error/unexpected body must raise ``ProviderError`` (→ provider_error) here
    rather than a bare ``KeyError``/``IndexError`` that escapes the judge client
    with no verdict event. A context-window rejection (OpenAI's canonical
    ``context_length_exceeded`` code) raises the more specific
    ``ProviderContextOverflow`` so the process stage records context_overflow
    [PR-9]."""
    if "error" in resp:
        err = resp["error"]
        if isinstance(err, dict) and err.get("code") == "context_length_exceeded":
            raise ProviderContextOverflow(f"openai context overflow: {err}")
        raise ProviderError(f"openai error response: {err}")
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"unexpected openai response shape: {resp}") from e


class OpenAIProvider(Provider):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        model = model_id.split("/", 1)[1]
        body = {"model": model, "temperature": temperature, "messages": messages,
                "max_tokens": MAX_OUTPUT_TOKENS}  # uniform cap [F-M-J4]
        self.last_usage = None  # F-M-J3
        resp = post_json(
            "https://api.openai.com/v1/chat/completions",
            body,
            {"authorization": f"Bearer {require_key('OPENAI_API_KEY')}"},
        )
        usage = resp.get("usage") or {}
        self.last_usage = normalize_usage(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return _content(resp)
