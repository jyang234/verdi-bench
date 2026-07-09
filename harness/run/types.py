"""Shared run-stage types: Task, TrialRequest, EngineResult, RunConfig.

These are engine-agnostic — the seam (``run_trial``) speaks only these types, so
no module outside ``engines/harbor.py`` needs to know Harbor exists [AC-1].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from ..adapters.base import Outcome, Provenance, Quotas
from ..schema.experiment import Arm

DEFAULT_TIMEOUT_S = 1800  # 30 minutes [D002]

# The single default trial resource envelope [refactor 04 §4]. Pinned per trial
# and recorded in provenance so both arms face identical quotas (D003/AC-6). One
# definition, referenced by RunConfig and RunSettings and the run.config.yaml
# null-fallback — previously restated at three sites that could silently drift.
DEFAULT_QUOTAS = Quotas(cpus=2.0, mem="4g")


@dataclass
class Task:
    """A unit of work. ``image`` is a pinned image ref/digest [D005]."""

    id: str
    prompt: str
    image: str = "verdi-bench/fake-agent@sha256:" + "0" * 64
    timeout_s: Optional[int] = None
    # canary strings seeded into holdouts — must never reach the trial [AC-9]
    holdout_canaries: list[str] = field(default_factory=list)
    # FAKE-ENGINE ONLY: scripts deterministic behavior for tests.
    fake_behavior: dict = field(default_factory=dict)
    # content sha of this task version — the scheduler compares (id, task_sha)
    # against the flake quarantine so quarantine is version-scoped [D-2].
    task_sha: Optional[str] = None
    # EnvironmentSpec [refactor 03 §5, A3]: a task's declared environment. `files`
    # (relative path → contents) are staged into /workspace pre-trial by BOTH
    # engines; `env` (NAME → VALUE, never secrets) is injected by Harbor after the
    # provider-key env; `extra_hosts` extends the derived proxy allowlist (consumed
    # by the settings/egress path, not the engine).
    files: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    extra_hosts: list = field(default_factory=list)


@dataclass
class ProxyConfig:
    """Metering-proxy egress configuration [AC-3, D001].

    ``allowlist`` = reachable hosts. Everything else attempted from inside a
    trial container is a logged violation, flagged on the record — tolerated as
    data, never silently allowed. ``infra_hosts`` [EVAL-20 AC-6] is the
    non-model subset of the allowlist (package registries, mirrors), carried
    separately so per-trial egress attestation can tell "declared
    infrastructure" from "should be attributable to a declared model".

    ``reverse_endpoints`` [RN-11] maps an upstream host to the metering proxy's
    in-network plain-HTTP reverse listener for it (``http://<ip>:<port>``, WITHOUT
    the ``/t/<trial>`` suffix — the engine appends that per trial). It steers a
    proxy-defiant client (the pinned claude CLI ignores HTTP(S)_PROXY,
    claude-code#14165) at a terminator that meters it per trial. Additive and
    default-empty: only the managed metering proxy populates it; the runtime-config
    (settings.py) path leaves it empty, as do external squid-based deployments.
    """

    allowlist: list[str] = field(default_factory=list)
    proxy_url: Optional[str] = None
    log_path: Optional[str] = None
    infra_hosts: list[str] = field(default_factory=list)
    reverse_endpoints: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def host_matches(host: str, declared: list[str]) -> bool:
        """Suffix-domain matching — the one definition both allowlisting and
        egress attestation use, so 'allowed' and 'attributable' cannot drift."""
        return any(host == a or host.endswith("." + a) for a in declared)

    def is_allowed(self, host: str) -> bool:
        return self.host_matches(host, self.allowlist)


@dataclass
class OtlpConfig:
    """In-trial OTLP trace-capture configuration [refactor 09 §4, A11].

    ``endpoint`` is where the arm's OTel exporter posts spans (the hermetic
    collector, ``http://verdi-trace-collector:4318``); ``log_path`` is the
    host-side envelope JSONL the post-run ladder reads to extract this trial's
    slice. ``None`` on a :class:`TrialRequest` means no collector is configured —
    zero behavior change. Additive and None-defaulted everywhere; the frozen
    ``request.json`` is untouched because this rides standard OTel env vars."""

    endpoint: str
    log_path: Optional[str] = None


@dataclass
class TrialRequest:
    """What an engine receives. There is **no field for holdout content** — the
    request type is the allowlist, so holdouts/canaries are unreachable by an
    engine by construction [AC-9], the same insulation-by-signature the judge
    packet uses."""

    trial_id: str
    task_id: str
    prompt: str
    image: str
    arm: Arm
    repetition: int
    workspace: Path
    quotas: Quotas
    timeout_s: int
    ts: str
    proxy: Optional[ProxyConfig] = None
    # In-trial OTLP trace capture [refactor 09 §4, A11]: when set, the engine
    # injects the OTel exporter env vars so the arm's spans reach the hermetic
    # collector, and the post-run ladder extracts this trial's slice. None =
    # no collector configured (zero behavior change).
    otlp: Optional[OtlpConfig] = None
    provider_keys: dict = field(default_factory=dict)  # injected at trial start [AC-8]
    fake_behavior: dict = field(default_factory=dict)  # FAKE ENGINE ONLY
    # EnvironmentSpec [refactor 03 §5, A3]: `files` staged into /workspace pre-trial
    # (both engines); `env` non-secret vars injected by Harbor after the provider
    # keys, never overriding them.
    files: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)


@dataclass
class EngineResult:
    """Raw engine output the seam normalizes into a TrialRecord."""

    outcome: Outcome
    native_log: dict
    artifacts_dir: Path
    exit_status: Optional[int] = None
    image_digest: Optional[str] = None
    agent_binary_version: Optional[str] = None
    harbor_version: Optional[str] = None
    engine: str = "fake"
    quotas: Optional[Quotas] = None
    egress_violation: bool = False
    egress_attempts: list[str] = field(default_factory=list)
    executed_at: Optional[str] = None
    # proxy-metered cost, kept as a cross-check signal only [risks §10]
    proxy_metered_cost: Optional[float] = None
    # refactor 09 §4/§5: sha256 of the persisted otlp_spans.json artifact the shared
    # _read_span_log wrote, or None (no collector configured, or span_log_missing).
    spans_sha: Optional[str] = None
    # machine-readable reason an infra failure occurred, set by the engine so the
    # scheduler ledgers a real reason instead of a fake-only placeholder [RN-14]
    failure_reason: Optional[str] = None


class Engine(Protocol):
    name: str

    def run(self, request: TrialRequest) -> EngineResult: ...


@dataclass
class RunConfig:
    engine: Engine
    default_timeout_s: int = DEFAULT_TIMEOUT_S
    quotas: Quotas = field(default_factory=lambda: DEFAULT_QUOTAS.model_copy())
    proxy: Optional[ProxyConfig] = None
    # In-trial OTLP trace capture [refactor 09 §4, A11]: threaded onto each
    # TrialRequest by the seam. None = no collector configured.
    otlp: Optional[OtlpConfig] = None
    redact_extra_patterns: list[str] = field(default_factory=list)
    provider_keys: dict = field(default_factory=dict)
    # PRA-M2: per-arm allowlist of provider-key NAMES. When set, an arm's
    # container receives ONLY the keys named for it — arm A never sees arm B's
    # provider key (a least-privilege fix for "insulated arms"; also keeps
    # per-provider cost attributable). None = the pre-M2 behavior (every arm gets
    # every key), preserved for the single-provider common case.
    provider_key_names_by_arm: Optional[dict] = None
