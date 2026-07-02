"""Minimal HTTP helper shared by the real provider clients.

Uses stdlib urllib so egress rides the environment's ``HTTP(S)_PROXY`` (the
metering proxy). Faults map to the provider exception hierarchy.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import ProviderError, ProviderTimeout


def post_json(url: str, payload: dict, headers: dict, *, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raise ProviderError(f"HTTP {e.code}: {e.reason}") from e
    except TimeoutError as e:  # pragma: no cover
        raise ProviderTimeout(str(e)) from e
    except urllib.error.URLError as e:  # pragma: no cover
        raise ProviderError(str(e)) from e


def require_key(env_var: str) -> str:
    key = os.environ.get(env_var)
    if not key:  # pragma: no cover - exercised only in real runs
        raise ProviderError(f"missing {env_var}; provider keys are env-injected at trial start")
    return key
