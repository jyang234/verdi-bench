"""Operational run settings [EVAL-4 §M2, AC-3/AC-6/AC-8, RN-13, EVAL-4-D-9].

Proxy, quotas, and provider-key wiring are **operational** config — not part of
the pre-registered, sha-locked ``experiment.yaml`` and never written to the
ledger. They are resolved at ``bench run`` from an optional ``run.config.yaml``
in the experiment directory plus the process environment:

* ``proxy.{url,allowlist,log_path}`` → the metering :class:`ProxyConfig` [AC-3]
* ``quotas.{cpus,mem}``              → pinned per trial, recorded in provenance [AC-6/D003]
* ``provider_key_names``             → VALUES read from the env by name and
                                       injected at trial start, never persisted [AC-8]
* ``reuse_control.bundle``           → operational control-reuse bundle path

The file's *shape* is parsed once by :class:`RunConfigFile` (refactor 04 §4),
replacing an isinstance ladder; the spec/env-dependent resolution (spec-derived
egress allowlist, provider-key VALUES) stays here. An absent file yields
conservative defaults (no proxy ⇒ ``--network none``, default quotas, no keys),
so the fake path and un-configured runs behave exactly as before. ``env`` is
injectable so the resolution is deterministically testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from ..adapters.base import Quotas
from .egress import proxy_config, spec_allowlist
from .types import DEFAULT_QUOTAS, OtlpConfig, ProxyConfig

RUN_CONFIG_FILENAME = "run.config.yaml"


class MissingProviderKeyError(RuntimeError):
    """A provider key named in run.config.yaml is absent from the env [D-P3-1].

    Fail closed: an arm that runs unauthenticated biases the A/B comparison. We
    still never *invent* a value [AC-8]; we refuse to run when a named key cannot
    be resolved."""


# --- run.config.yaml shape [refactor 04 §4] --------------------------------
class ProxyBlock(BaseModel):
    """The ``proxy:`` block. Lenient to unknown keys (operational file, not a
    sha-locked contract); the mapping check keeps the exact legacy message."""

    model_config = ConfigDict(extra="ignore")
    url: Optional[str] = None
    allowlist: Optional[list[str]] = None
    log_path: Optional[str] = None
    # Opt-in [refactor 04 §1]: when true the harness stands up the managed
    # metering proxy (MeteringProxy) around the run and injects its own url +
    # log_path, so the operator does not hand-roll the 7-step docker lifecycle.
    managed: bool = False


class OtlpBlock(BaseModel):
    """The ``otlp:`` block — in-trial OTLP trace capture [refactor 09 §3/§4, A11].

    Two forms, both additive: ``managed: true`` stands the hermetic collector up
    around the run (TraceCollector) and injects its own endpoint + log_path; or
    an explicit ``endpoint`` (+ optional ``log_path``) points trials at an
    already-running collector. Absent ⇒ no capture (zero behavior change)."""

    model_config = ConfigDict(extra="ignore")
    managed: bool = False
    endpoint: Optional[str] = None
    log_path: Optional[str] = None


class QuotasBlock(BaseModel):
    """The ``quotas:`` block. A missing/null field falls back to the pinned
    default — an explicit ``null`` must NOT silently un-pin a quota (D003/AC-6),
    so resolution treats ``None`` as 'use the default', never as 'no quota'."""

    model_config = ConfigDict(extra="ignore")
    cpus: Optional[float] = None
    mem: Optional[str] = None


class ReuseControlBlock(BaseModel):
    """The ``reuse_control:`` block — operational control-reuse, never the
    sha-locked spec. Only ``bundle`` is consumed today."""

    model_config = ConfigDict(extra="ignore")
    bundle: Optional[str] = None


class RunConfigFile(BaseModel):
    """Typed reader for ``run.config.yaml`` [refactor 04 §4].

    One parse of the file's shape (proxy, quotas, provider-key names, reuse
    control), replacing the hand-rolled isinstance ladder and the CLI's second
    raw read of the same file. Unknown top-level keys are ignored, matching the
    previous ``data.get(...)`` leniency; the block mapping checks keep their
    exact legacy refusal strings (several tests pin the egress ones downstream).
    """

    model_config = ConfigDict(extra="ignore")
    proxy: Optional[ProxyBlock] = None
    otlp: Optional[OtlpBlock] = None
    quotas: Optional[QuotasBlock] = None
    provider_key_names: list[str] = Field(default_factory=list)
    provider_key_names_by_arm: Optional[dict[str, list[str]]] = None
    reuse_control: Optional[ReuseControlBlock] = None

    @field_validator("proxy", mode="before")
    @classmethod
    def _proxy_is_mapping(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError(
                f"run.config.yaml 'proxy' must be a mapping, got {type(v).__name__}"
            )
        return v

    @field_validator("otlp", mode="before")
    @classmethod
    def _otlp_is_mapping(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError(
                f"run.config.yaml 'otlp' must be a mapping, got {type(v).__name__}"
            )
        return v

    @field_validator("quotas", mode="before")
    @classmethod
    def _quotas_is_mapping(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError(
                f"run.config.yaml 'quotas' must be a mapping, got {type(v).__name__}"
            )
        return v

    @field_validator("provider_key_names", mode="before")
    @classmethod
    def _names_null_to_empty(cls, v):
        # absent/null ⇒ [] (the legacy ``... or []``); a list validates as-is.
        return [] if v is None else v

    @field_validator("provider_key_names_by_arm", mode="before")
    @classmethod
    def _by_arm_is_mapping(cls, v):
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError(
                "run.config.yaml 'provider_key_names_by_arm' must be a mapping "
                "{arm_name: [key_names]}"
            )
        # each arm's names default to [] (legacy ``list(names or [])``).
        return {arm: (names or []) for arm, names in v.items()}

    @field_validator("reuse_control", mode="before")
    @classmethod
    def _reuse_control_lenient(cls, v):
        # the CLI historically ignored a non-mapping reuse_control silently.
        return v if isinstance(v, dict) else None

    @classmethod
    def parse(cls, data: Optional[dict]) -> "RunConfigFile":
        """Validate a raw run.config.yaml mapping, surfacing the block mapping
        checks as their plain :class:`ValueError` (not a pydantic-wrapped one),
        the same unwrap discipline :meth:`ExperimentSpec.from_dict` uses."""
        try:
            return cls.model_validate(data or {})
        except ValidationError as e:
            for err in e.errors():
                wrapped = err.get("ctx", {}).get("error")
                if isinstance(wrapped, ValueError) and not isinstance(
                    wrapped, ValidationError
                ):
                    raise wrapped from e
            raise


@dataclass
class RunSettings:
    """Resolved operational parameters for a run (never the pre-registered spec)."""

    proxy: Optional[ProxyConfig] = None
    # Opt-in managed metering proxy [refactor 04 §1]: the engine run is wrapped in
    # MeteringProxy.managed(...) when set, standing the proxy up and tearing it
    # down around the schedule. Resolved from run.config.yaml's proxy.managed.
    proxy_managed: bool = False
    # In-trial OTLP trace capture [refactor 09 §4, A11]: the explicit collector
    # config (endpoint + log_path) trials post to, or None. When otlp_managed is
    # set, the run wraps the schedule in TraceCollector.managed(...) and this stays
    # None (the managed lifecycle supplies its own endpoint + log_path).
    otlp: Optional[OtlpConfig] = None
    otlp_managed: bool = False
    quotas: Quotas = field(default_factory=lambda: DEFAULT_QUOTAS.model_copy())
    provider_keys: dict = field(default_factory=dict)
    # PRA-M2: optional per-arm provider-key NAME allowlist {arm: [names]}. None
    # means every arm gets every key (pre-M2 behavior).
    provider_key_names_by_arm: Optional[dict] = None
    # Operational control-reuse bundle path, resolved from run.config.yaml's
    # reuse_control.bundle (absolute, or relative to the experiment dir). None
    # when unset. Surfaced here so the run CLI reuses this single parse instead
    # of re-reading the file [refactor 04 §4].
    reuse_control_bundle: Optional[Path] = None


def load_run_settings(
    experiment_dir,
    env: Optional[Mapping[str, str]] = None,
    *,
    spec=None,
    task_extra_hosts: Optional[list[str]] = None,
) -> RunSettings:
    """Resolve run settings from ``<experiment_dir>/run.config.yaml`` + ``env``.

    Provider-key VALUES come from ``env`` by name; a value is never invented
    [AC-8]. A key *named* in the config but absent from the environment fails the
    run loudly (:class:`MissingProviderKeyError`, D-P3-1) — an unauthenticated arm
    would bias the A/B — rather than silently dropping to no key.

    When ``spec`` declares egress hosts (``model_hosts``/``infra_hosts``,
    EVAL-20 AC-6 [D003]), the proxy allowlist is DERIVED from those locked
    bytes; a run.config.yaml that also carries an allowlist then conflicts with
    the pre-registration and is refused loudly rather than silently overridden.
    A spec declaring no hosts keeps the pre-EVAL-20 behavior exactly.
    """
    env = os.environ if env is None else env
    # A3: task extra_hosts extend the spec-derived allowlist (for all arms); inert
    # when the spec pre-registers no hosts (spec_allowlist keeps runtime-mode).
    declared = spec_allowlist(spec, task_extra_hosts) if spec is not None else []
    infra = sorted(spec.infra_hosts) if spec is not None else []
    path = Path(experiment_dir) / RUN_CONFIG_FILENAME
    if not path.exists():
        if declared:
            # A pre-registered egress declaration with nothing to enforce it is
            # an inconsistent operational state — refuse loudly, never run with
            # the locked contract silently void [EVAL-20 AC-6].
            raise ValueError(
                "the locked spec pre-registers egress hosts "
                f"(model_hosts/infra_hosts) but {RUN_CONFIG_FILENAME} is absent; "
                "the derived allowlist cannot be enforced — configure proxy.url "
                "or remove the declared hosts before locking [EVAL-20 AC-6]"
            )
        return RunSettings()
    cfg = RunConfigFile.parse(yaml.safe_load(path.read_text(encoding="utf-8")))

    proxy = None
    proxy_managed = False
    if cfg.proxy is not None:
        proxy_managed = cfg.proxy.managed
        if proxy_managed and cfg.proxy.url:
            # The harness stands up AND addresses its own proxy when managed;
            # an operator-supplied url is contradictory — refuse, never silently
            # override [refactor 04 §1, fail-loudly].
            raise ValueError(
                "run.config.yaml sets proxy.managed: true but also proxy.url; the "
                "managed proxy provides its own url — remove proxy.url [refactor 04 §1]"
            )
        if declared and cfg.proxy.allowlist is not None:
            raise ValueError(
                "run.config.yaml declares a proxy allowlist but the locked spec "
                "pre-registers egress hosts (model_hosts/infra_hosts); the "
                "allowlist derives from the spec — remove it from "
                f"{RUN_CONFIG_FILENAME} [EVAL-20 AC-6]"
            )
        proxy = proxy_config(
            declared if declared else cfg.proxy.allowlist,
            proxy_url=cfg.proxy.url,
            log_path=cfg.proxy.log_path,
            infra_hosts=infra,
        )
    if declared and proxy is None:
        raise ValueError(
            "the locked spec pre-registers egress hosts (model_hosts/infra_hosts) "
            f"but {RUN_CONFIG_FILENAME} configures no proxy; the derived allowlist "
            "cannot be enforced — configure proxy.url or remove the declared hosts "
            "before locking [EVAL-20 AC-6]"
        )

    # In-trial OTLP trace capture [refactor 09 §4, A11]. `managed: true` stands the
    # hermetic collector up around the run (the lifecycle supplies its own endpoint
    # + log_path, so an operator-supplied endpoint is contradictory — refuse, like
    # proxy.managed + proxy.url); the explicit form points trials at an
    # already-running collector, where an endpoint is required.
    otlp = None
    otlp_managed = False
    if cfg.otlp is not None:
        otlp_managed = cfg.otlp.managed
        if otlp_managed and cfg.otlp.endpoint:
            raise ValueError(
                "run.config.yaml sets otlp.managed: true but also otlp.endpoint; the "
                "managed collector provides its own endpoint — remove otlp.endpoint "
                "[refactor 09 §4]"
            )
        if not otlp_managed:
            if not cfg.otlp.endpoint:
                raise ValueError(
                    "run.config.yaml 'otlp' sets no endpoint and is not managed; set "
                    "otlp.endpoint or otlp.managed: true [refactor 09 §4]"
                )
            otlp = OtlpConfig(endpoint=cfg.otlp.endpoint, log_path=cfg.otlp.log_path)

    # An explicit ``null`` must NOT silently un-pin a quota (which would break
    # cross-arm comparability, D003/AC-6); a missing or null value falls back to
    # the pinned default (DEFAULT_QUOTAS, the single source of the 2.0/4g values).
    qblock = cfg.quotas or QuotasBlock()
    quotas = Quotas(
        cpus=DEFAULT_QUOTAS.cpus if qblock.cpus is None else qblock.cpus,
        mem=DEFAULT_QUOTAS.mem if qblock.mem is None else qblock.mem,
    )

    # The file lists key NAMES; the VALUES are read from the environment and are
    # never written to the file or the ledger [AC-8]. A named-but-absent key fails
    # the run loudly rather than silently dropping to an unauthenticated arm [D-P3-1].
    # PRA-M2: keys may be declared flat (provider_key_names → every arm) and/or
    # per-arm (provider_key_names_by_arm → only that arm). The VALUES for the
    # UNION are read from the env (each named-but-absent key still fails loud);
    # the per-arm NAME lists drive which arm's container receives which key.
    by_arm = cfg.provider_key_names_by_arm
    all_names: list[str] = list(cfg.provider_key_names)
    if by_arm is not None:
        for names in by_arm.values():
            all_names.extend(names)

    provider_keys = {}
    for name in all_names:
        if name in provider_keys:
            continue  # union: read each value once
        if name not in env:
            raise MissingProviderKeyError(
                f"provider key {name!r} is named in {RUN_CONFIG_FILENAME} but absent "
                "from the environment; an unauthenticated arm biases the A/B. Set "
                f"{name} in the env or remove it from provider_key_names."
            )
        provider_keys[name] = env[name]

    reuse_bundle: Optional[Path] = None
    if cfg.reuse_control is not None and cfg.reuse_control.bundle:
        b = Path(cfg.reuse_control.bundle)
        reuse_bundle = b if b.is_absolute() else Path(experiment_dir) / b

    return RunSettings(
        proxy=proxy, proxy_managed=proxy_managed,
        otlp=otlp, otlp_managed=otlp_managed, quotas=quotas,
        provider_keys=provider_keys, provider_key_names_by_arm=by_arm,
        reuse_control_bundle=reuse_bundle,
    )
