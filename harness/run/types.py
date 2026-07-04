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


@dataclass
class ProxyConfig:
    """Metering-proxy egress configuration [AC-3, D001].

    ``allowlist`` = reachable hosts. Everything else attempted from inside a
    trial container is a logged violation, flagged on the record — tolerated as
    data, never silently allowed. ``infra_hosts`` [EVAL-13 AC-6] is the
    non-model subset of the allowlist (package registries, mirrors), carried
    separately so per-trial egress attestation can tell "declared
    infrastructure" from "should be attributable to a declared model".
    """

    allowlist: list[str] = field(default_factory=list)
    proxy_url: Optional[str] = None
    log_path: Optional[str] = None
    infra_hosts: list[str] = field(default_factory=list)

    @staticmethod
    def host_matches(host: str, declared: list[str]) -> bool:
        """Suffix-domain matching — the one definition both allowlisting and
        egress attestation use, so 'allowed' and 'attributable' cannot drift."""
        return any(host == a or host.endswith("." + a) for a in declared)

    def is_allowed(self, host: str) -> bool:
        return self.host_matches(host, self.allowlist)


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
    provider_keys: dict = field(default_factory=dict)  # injected at trial start [AC-8]
    fake_behavior: dict = field(default_factory=dict)  # FAKE ENGINE ONLY


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
    quotas: Quotas = field(default_factory=lambda: Quotas(cpus=2.0, mem="4g"))
    proxy: Optional[ProxyConfig] = None
    redact_extra_patterns: list[str] = field(default_factory=list)
    provider_keys: dict = field(default_factory=dict)
