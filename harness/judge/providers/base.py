"""Provider interface [EVAL-2 §3].

Thin per-provider clients behind one ``complete(model_id, messages, temperature)
-> text`` interface, versions pinned. Deliberately not a heavyweight router — pins
stay explicit and refusal/timeout semantics stay under our control. There is **no
vendor allow/deny list**; any provider prefix resolves [D001].
"""

from __future__ import annotations

from typing import Protocol


class ProviderError(RuntimeError):
    """Generic provider failure → CANT_JUDGE(provider_error)."""


class ProviderTimeout(ProviderError):
    """→ CANT_JUDGE(timeout)."""


class ProviderRefusal(ProviderError):
    """The model refused → CANT_JUDGE(refusal)."""


# F-M-J4: one output cap for every provider. Anthropic hardcoded 2048 while
# OpenAI/Google set none — a truncated verdict JSON became a CANT_JUDGE(parse)
# on one vendor only, an asymmetric failure mode. Sized generously for the
# verdict JSON (a few hundred tokens) plus reasoning preamble.
MAX_OUTPUT_TOKENS = 4096


class ProviderContextOverflow(ProviderError):
    """The provider rejected the request as over its context window [PR-9].

    Distinct from a generic provider error so the process stage can record
    ``CANT_SCORE(context_overflow)`` (with the provider's own token counts when
    it reports them) instead of a generic ``provider_error`` — the provider's
    verdict on context size is more specific than our pre-flight chars/4 gate."""

    def __init__(self, message: str, *, prompt_tokens: int | None = None,
                 max_tokens: int | None = None) -> None:
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.max_tokens = max_tokens


class Provider(Protocol):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str: ...


def provider_failure_reason(exc: ProviderError) -> str:
    """Map a provider exception to a closed fail-closed reason string.

    Shared by the judge (``CantJudgeReason``) and process (``CantScoreReason``)
    stages so the two cannot drift on how a timeout / refusal / generic error is
    classified — the exact ``parse`` vs ``unparsed`` drift Phase 3 fixed. The
    returned string is a member of both enums' closed value sets. ``ProviderTimeout``
    and ``ProviderRefusal`` subclass ``ProviderError``, so order matters.
    """
    if isinstance(exc, ProviderTimeout):
        return "timeout"
    if isinstance(exc, ProviderRefusal):
        return "refusal"
    if isinstance(exc, ProviderContextOverflow):
        return "context_overflow"
    return "provider_error"


def get_provider(model_id: str) -> Provider:
    """Resolve a provider by the ``<provider>/...`` prefix. No allow/deny list."""
    provider = model_id.split("/", 1)[0]
    if provider == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider()
    if provider == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider()
    if provider == "google":
        from .google import GoogleProvider

        return GoogleProvider()
    if provider == "fake":
        # The deterministic no-network provider — the analog of `--engine fake`,
        # selected by a `fake/...` model prefix; serves judge verdicts and process
        # scores. Not a vendor allow/deny entry: any prefix still resolves or
        # fails closed [D001].
        from .fake import DeterministicFakeProvider

        return DeterministicFakeProvider()
    raise ProviderError(f"no client for provider prefix {provider!r}")
