"""Minimal HTTP helper shared by the real provider clients.

Uses stdlib urllib so egress rides the environment's ``HTTP(S)_PROXY`` (the
metering proxy). Faults map to the provider exception hierarchy.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

from .base import ProviderError, ProviderTimeout

# F-M-J4: bounded, fixed backoff for transient HTTP faults (429/5xx/timeouts).
# One 429 previously failed the whole judge batch closed. Fixed delays — no
# jitter needed: this seam already owns wall-clock (timeouts), and the retry
# count/delays are constants, so behavior stays reproducible in outcome terms.
RETRY_ATTEMPTS = 3
RETRY_DELAYS_S = (2.0, 4.0)


def _retryable_http(code: int) -> bool:
    return code == 429 or code >= 500


def _classify_urlerror(e: urllib.error.URLError) -> ProviderError:
    """Map a ``URLError`` to the right provider exception [JD-13].

    A connect-phase timeout reaches us as a ``URLError`` wrapping a
    ``TimeoutError``/``socket.timeout`` in ``.reason`` (a read-phase timeout
    raises a bare ``TimeoutError`` instead) — it must classify as ``timeout``,
    not a generic ``provider_error``.
    """
    if isinstance(e.reason, (TimeoutError, socket.timeout)):
        return ProviderTimeout(str(e))
    return ProviderError(str(e))


def post_json(url: str, payload: dict, headers: dict, *, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "content-type": "application/json"})
    last: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_DELAYS_S[attempt - 1])
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if _retryable_http(e.code) and attempt < RETRY_ATTEMPTS - 1:
                last = e
                continue
            raise ProviderError(f"HTTP {e.code}: {e.reason}") from e
        except TimeoutError as e:
            if attempt < RETRY_ATTEMPTS - 1:
                last = e
                continue
            raise ProviderTimeout(str(e)) from e
        except urllib.error.URLError as e:
            classified = _classify_urlerror(e)
            if isinstance(classified, ProviderTimeout) and attempt < RETRY_ATTEMPTS - 1:
                last = e
                continue
            raise classified from e
    raise ProviderError(f"exhausted retries: {last}")  # pragma: no cover - loop always raises/returns


def require_key(env_var: str) -> str:
    key = os.environ.get(env_var)
    if not key:  # pragma: no cover - exercised only in real runs
        raise ProviderError(f"missing {env_var}; provider keys are env-injected at trial start")
    return key
