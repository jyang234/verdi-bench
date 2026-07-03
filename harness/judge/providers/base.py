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
        # The deterministic no-network judge — the judge analog of `--engine
        # fake`, selected by a `fake/...` judge-model prefix. Not a vendor
        # allow/deny entry: any prefix still resolves or fails closed [D001].
        from .fake import DeterministicFakeJudge

        return DeterministicFakeJudge()
    raise ProviderError(f"no client for provider prefix {provider!r}")
