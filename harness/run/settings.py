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
from .egress import proxy_config, spec_allowlist
from .types import ProxyConfig

RUN_CONFIG_FILENAME = "run.config.yaml"


class MissingProviderKeyError(RuntimeError):
    """A provider key named in run.config.yaml is absent from the env [D-P3-1].

    Fail closed: an arm that runs unauthenticated biases the A/B comparison. We
    still never *invent* a value [AC-8]; we refuse to run when a named key cannot
    be resolved."""


@dataclass
class RunSettings:
    """Resolved operational parameters for a run (never the pre-registered spec)."""

    proxy: Optional[ProxyConfig] = None
    quotas: Quotas = field(default_factory=lambda: Quotas(cpus=2.0, mem="4g"))
    provider_keys: dict = field(default_factory=dict)


def load_run_settings(
    experiment_dir, env: Optional[Mapping[str, str]] = None, *, spec=None
) -> RunSettings:
    """Resolve run settings from ``<experiment_dir>/run.config.yaml`` + ``env``.

    Provider-key VALUES come from ``env`` by name; a value is never invented
    [AC-8]. A key *named* in the config but absent from the environment fails the
    run loudly (:class:`MissingProviderKeyError`, D-P3-1) — an unauthenticated arm
    would bias the A/B — rather than silently dropping to no key.

    When ``spec`` declares egress hosts (``model_hosts``/``infra_hosts``,
    EVAL-13 AC-6 [D003]), the proxy allowlist is DERIVED from those locked
    bytes; a run.config.yaml that also carries an allowlist then conflicts with
    the pre-registration and is refused loudly rather than silently overridden.
    A spec declaring no hosts keeps the pre-EVAL-13 behavior exactly.
    """
    env = os.environ if env is None else env
    path = Path(experiment_dir) / RUN_CONFIG_FILENAME
    if not path.exists():
        return RunSettings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    declared = spec_allowlist(spec) if spec is not None else []
    infra = sorted(spec.infra_hosts) if spec is not None else []

    proxy = None
    pcfg = data.get("proxy")
    if pcfg is not None:
        if not isinstance(pcfg, dict):
            raise ValueError(
                f"run.config.yaml 'proxy' must be a mapping, got {type(pcfg).__name__}"
            )
        if declared and pcfg.get("allowlist") is not None:
            raise ValueError(
                "run.config.yaml declares a proxy allowlist but the locked spec "
                "pre-registers egress hosts (model_hosts/infra_hosts); the "
                "allowlist derives from the spec — remove it from "
                f"{RUN_CONFIG_FILENAME} [EVAL-13 AC-6]"
            )
        proxy = proxy_config(
            declared if declared else pcfg.get("allowlist"),
            proxy_url=pcfg.get("url"),
            log_path=pcfg.get("log_path"),
            infra_hosts=infra,
        )

    qcfg = data.get("quotas")
    if qcfg is None:
        qcfg = {}
    elif not isinstance(qcfg, dict):
        raise ValueError(
            f"run.config.yaml 'quotas' must be a mapping, got {type(qcfg).__name__}"
        )
    # An explicit ``null`` must NOT silently un-pin a quota (which would break
    # cross-arm comparability, D003/AC-6); a missing or null value falls back to
    # the pinned default.
    cpus = qcfg.get("cpus")
    mem = qcfg.get("mem")
    quotas = Quotas(cpus=2.0 if cpus is None else cpus, mem="4g" if mem is None else mem)

    # The file lists key NAMES; the VALUES are read from the environment and are
    # never written to the file or the ledger [AC-8]. A named-but-absent key fails
    # the run loudly rather than silently dropping to an unauthenticated arm [D-P3-1].
    provider_keys = {}
    for name in data.get("provider_key_names") or []:
        if name not in env:
            raise MissingProviderKeyError(
                f"provider key {name!r} is named in {RUN_CONFIG_FILENAME} but absent "
                "from the environment; an unauthenticated arm biases the A/B. Set "
                f"{name} in the env or remove it from provider_key_names."
            )
        provider_keys[name] = env[name]
    return RunSettings(proxy=proxy, quotas=quotas, provider_keys=provider_keys)
