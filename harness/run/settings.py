"""Operational run settings [EVAL-4 §M2, AC-3/AC-6/AC-8, RN-13, EVAL-4-D-9].

Proxy, quotas, and provider-key wiring are **operational** config — not part of
the pre-registered, sha-locked ``experiment.yaml`` and never written to the
ledger. They are resolved at ``bench run`` from an optional ``run.config.yaml``
in the experiment directory plus the process environment:

* ``proxy.{url,allowlist,log_path}`` → the metering :class:`ProxyConfig` [AC-3]
* ``quotas.{cpus,mem}``              → pinned per trial, recorded in provenance [AC-6/D003]
* ``provider_key_names``             → VALUES read from the env by name and
                                       injected at trial start, never persisted [AC-8]

An absent file yields conservative defaults (no proxy ⇒ ``--network none``,
default quotas, no keys), so the fake path and un-configured runs behave exactly
as before. ``env`` is injectable so the resolution is deterministically testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

import yaml

from ..adapters.base import Quotas
from .egress import proxy_config
from .types import ProxyConfig

RUN_CONFIG_FILENAME = "run.config.yaml"


@dataclass
class RunSettings:
    """Resolved operational parameters for a run (never the pre-registered spec)."""

    proxy: Optional[ProxyConfig] = None
    quotas: Quotas = field(default_factory=lambda: Quotas(cpus=2.0, mem="4g"))
    provider_keys: dict = field(default_factory=dict)


def load_run_settings(
    experiment_dir, env: Optional[Mapping[str, str]] = None
) -> RunSettings:
    """Resolve run settings from ``<experiment_dir>/run.config.yaml`` + ``env``.

    Provider-key VALUES come from ``env`` by name; a named key absent from the
    environment is simply not injected (a value is never invented) [AC-8].
    """
    env = os.environ if env is None else env
    path = Path(experiment_dir) / RUN_CONFIG_FILENAME
    if not path.exists():
        return RunSettings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    proxy = None
    pcfg = data.get("proxy")
    if pcfg:
        proxy = proxy_config(
            pcfg.get("allowlist"),
            proxy_url=pcfg.get("url"),
            log_path=pcfg.get("log_path"),
        )

    qcfg = data.get("quotas") or {}
    quotas = Quotas(cpus=qcfg.get("cpus", 2.0), mem=qcfg.get("mem", "4g"))

    # The file lists key NAMES; the VALUES are read from the environment and are
    # never written to the file or the ledger [AC-8].
    provider_keys = {
        name: env[name] for name in (data.get("provider_key_names") or []) if name in env
    }
    return RunSettings(proxy=proxy, quotas=quotas, provider_keys=provider_keys)
