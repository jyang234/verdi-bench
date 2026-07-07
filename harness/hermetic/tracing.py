"""Managed OTLP trace-collector lifecycle [refactor 09 §3].

:class:`TraceCollector` is a :class:`~harness.hermetic.sidecar.ManagedSidecar`
[refactor 11 §G2] — it inherits the context-manager skeleton, the readiness
*probe* of 4318 (never a fixed timer), container removal, and log-dir/basename
resolution — with two deliberate differences: it attaches **only** to
``METERED_NETWORK`` (``--internal``) and never to the egress network (it has no
outbound needs, so span data physically cannot leave the host [refactor 09 §3,
§6]), and it deletes the raw envelope log on teardown [D-09-1, below].

The collector is the stdlib ``_collector_container.py``, mounted read-only into a
pinned ``python:3.12-alpine`` and run in place — no image build. Its envelope
JSONL contract is frozen [refactor 09 §2].

**DECISION D-09-1** (raw-log retention): the shared host-side envelope log holds
raw (possibly secret/identity-bearing) span bodies that ``redact_artifacts``
cannot see inside base64/protobuf, so it cannot be made safe to keep. The
lifecycle therefore **deletes it on teardown** (the ``_pre_teardown`` override)
after each trial has already extracted its per-trial slice into the redacted,
sha-bound ``otlp_spans.json``; ``keep_raw=True`` retains it as an explicitly
operator-tier file.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict

# Absolute imports (not ``from .docker import ...``): the AST seam sweep
# (tests/test_eval4_seam.py) flags a bare module name ``docker`` in an import.
from harness.errors import VerdiRefusal
from harness.hermetic.docker import DockerClient, HardenedCommand
from harness.hermetic.metering import PROXY_BASE_IMAGE
from harness.hermetic.network import METERED_NETWORK, create_network, remove_network
from harness.hermetic.sidecar import ManagedSidecar, remove_managed_container

# The collector shares the proxy's pinned base image (the same ``python3`` runs
# both stdlib sidecars) so the digest lives in exactly one place [refactor 04 §1].
# Overridable so a CI/other-platform runner can re-pin without a code change.
COLLECTOR_BASE_IMAGE = os.environ.get("VERDI_COLLECTOR_IMAGE", PROXY_BASE_IMAGE)

# The managed collector's container name — resolvable by name on the metered
# network, so a trial's ``OTEL_EXPORTER_OTLP_ENDPOINT=http://verdi-trace-collector:4318``
# finds it. Single-constant discipline, like ``MANAGED_PROXY_NAME``/``METERED_NETWORK``.
MANAGED_COLLECTOR_NAME = "verdi-trace-collector"
COLLECTOR_PORT = 4318
_CONTAINER_LOG = "/var/log/verdi/otlp.jsonl"


class CollectorConfig(BaseModel):
    """What :meth:`TraceCollector.__enter__` yields — the endpoint a trial's OTel
    exporter targets and the host-side envelope JSONL the extraction reads."""

    model_config = ConfigDict(extra="forbid")
    endpoint: str
    log_path: str


class TraceCollectorError(VerdiRefusal, RuntimeError):
    """The managed trace collector could not be stood up (no daemon, image, or it
    never became ready). Fail loudly — a run that proceeded believing capture was
    live while the collector was dead would lose spans silently [refactor 09 §3]."""


class TraceCollector(ManagedSidecar):
    """Context manager owning the trace collector's whole lifecycle [refactor 09 §3].

    A :class:`~harness.hermetic.sidecar.ManagedSidecar` whose divergences are the
    metered-only stand-up (no egress connect) and the D-09-1 delete of the raw
    envelope log on teardown [refactor 11 §G2].
    """

    port = COLLECTOR_PORT
    _ERROR_CLS = TraceCollectorError
    _NOUN = "trace collector"
    _DAEMON_UNAVAILABLE_MESSAGE = (
        "docker daemon is unavailable — cannot stand up the managed trace "
        "collector; run without otlp.managed or start docker"
    )
    _LOG_PREFIX = "verdi-otlp-"
    _DEFAULT_LOG_BASENAME = "otlp.jsonl"

    def __init__(
        self,
        *,
        log_path: Optional[Path] = None,
        keep_raw: bool = False,
        image: str = COLLECTOR_BASE_IMAGE,
        docker: Optional[DockerClient] = None,
        name: str = MANAGED_COLLECTOR_NAME,
    ) -> None:
        self._keep_raw = keep_raw
        self._collector_src = Path(__file__).resolve().parent / "_collector_container.py"
        # log-dir/basename resolution: an explicit path is honored as-is (its
        # basename rides into the container via COLLECTOR_LOG so a custom filename
        # is never left touched-but-empty beside otlp.jsonl — the 988af58 PROXY_LOG
        # lesson); an absent path gets a managed temp dir [refactor 11 §G2].
        super().__init__(log_path=log_path, image=image, docker=docker, name=name)

    @classmethod
    def managed(
        cls,
        *,
        log_path: Optional[Path] = None,
        keep_raw: bool = False,
        image: str = COLLECTOR_BASE_IMAGE,
    ) -> "TraceCollector":
        """Build a managed collector (see the class docstring). ``keep_raw`` opts out
        of the D-09-1 delete-on-teardown of the raw envelope log."""
        return cls(log_path=log_path, keep_raw=keep_raw, image=image)

    # --- divergent seams ------------------------------------------------------
    def _stand_up(self) -> None:
        """Stand the collector up on the SAME internal metered network the proxied
        trial is on, reachable there by name; it never connects to the egress
        network — the collector has no outbound needs [refactor 09 §3]."""
        create_network(self._docker, METERED_NETWORK, internal=True)
        cmd = (
            HardenedCommand()
            .detach()
            .name(self._name)
            .network(METERED_NETWORK)
            .harden()
            .user()
            # The basename rides into the container under the mounted dir (the
            # PROXY_LOG discipline), so a custom log path is honored exactly.
            .env_kv("COLLECTOR_LOG", f"{os.path.dirname(_CONTAINER_LOG)}/{self._logfile.name}")
            .volume(self._collector_src, "/verdi/collector.py", ro=True)
            .volume(self._logdir, os.path.dirname(_CONTAINER_LOG))
            .image(self._image)
            .arg("python3", "/verdi/collector.py")
            .build()
        )
        proc = self._docker.run(cmd, timeout_s=120)
        if proc.returncode != 0:
            raise TraceCollectorError(
                f"could not start the trace collector container: {proc.stderr.strip()}"
            )
        # NO egress connect — span data terminates on the host [refactor 09 §3, §6].

    def _config(self) -> CollectorConfig:
        return CollectorConfig(
            endpoint=f"http://{self._name}:{COLLECTOR_PORT}",
            log_path=str(self._logfile),
        )

    def _teardown_networks(self) -> None:
        # Best-effort: if a managed metering proxy still holds the metered network,
        # this rm is a no-op and the proxy's own teardown removes it [refactor 09 §3].
        remove_network(self._docker, METERED_NETWORK)

    def _pre_teardown(self) -> None:
        """D-09-1: the raw envelope log holds un-redactable secret/identity-bearing
        bodies, so it is deleted on teardown unless ``keep_raw`` retains it as an
        explicitly operator-tier file [refactor 09 §6]."""
        if self._keep_raw:
            return
        if self._owns_logdir:
            shutil.rmtree(self._logdir, ignore_errors=True)
        elif self._logfile.exists():
            self._logfile.unlink()


def teardown_managed(
    docker: Optional[DockerClient] = None,
    *,
    name: str = MANAGED_COLLECTOR_NAME,
    log_path: Optional[Path] = None,
    keep_raw: bool = False,
) -> None:
    """Remove the managed collector container + its metered network by their known
    names — the ``bench otlp down`` worker.

    A standalone teardown (the names are constants), so it does not need the
    originating :class:`TraceCollector`. It removes the metered network best-effort
    (a live metering proxy still holding it makes the rm a no-op), and applies the
    D-09-1 default: delete ``log_path`` unless ``keep_raw`` [refactor 09 §3, §6].
    """
    docker = docker or DockerClient()
    remove_managed_container(name, docker)
    remove_network(docker, METERED_NETWORK)
    if log_path is not None and not keep_raw:
        p = Path(log_path)
        if p.exists():
            p.unlink()
