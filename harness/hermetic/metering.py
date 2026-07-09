"""Managed metering-proxy lifecycle [refactor 04 §1].

:class:`MeteringProxy` replaces the 7-raw-docker-step lifecycle the shakedown
scripts hand-rolled (and were already diverging on) with one context manager:
``__enter__`` stands up the metered + egress networks and the packaged CONNECT
proxy with the resolved allowlist **injected**, waits for readiness by *probing*
(never a fixed timer), and yields a :class:`~harness.run.types.ProxyConfig`;
``__exit__`` always tears the whole thing down.

The context-manager skeleton, readiness probe, container removal, and
log-dir/basename resolution live once in :class:`~harness.hermetic.sidecar.ManagedSidecar`
[refactor 11 §G2]; this module keeps only the proxy's deliberate divergences —
the dual metered+egress network stand-up and the CONNECT allowlist injection.

The proxy is the stdlib ``_proxy_container.py``, mounted read-only into a pinned
``python:3.12-alpine`` and run in place — no image build. Its JSONL contract and
trial-id-as-userinfo auth are frozen [refactor 04 §1, §6].

Reverse listeners [RN-11]: additionally, for each allowlisted host the proxy binds
a plain-HTTP reverse listener (``VERDI_REVERSE_PORTS``) that terminates a
proxy-defiant client (the pinned claude CLI ignores HTTP(S)_PROXY,
claude-code#14165) and originates verified TLS upstream. ``_config`` resolves the
proxy's METERED_NETWORK IP (not its name — the bun binary's resolver bypasses
/etc/hosts and container-name DNS is unproven on this network) and yields
``reverse_endpoints`` so the engine can steer that client's base URL at the
listener. Purely additive: the CONNECT ``proxy_url`` keeps the name (frozen), and
squid-based external deployments simply do not set ``reverse_endpoints``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

# Absolute imports (not ``from .docker import ...``): the AST seam sweep
# (tests/test_eval4_seam.py) flags a bare module name ``docker`` in an import.
from harness.errors import VerdiRefusal
from harness.hermetic.docker import DockerClient, HardenedCommand
from harness.hermetic.network import (
    EGRESS_NETWORK,
    METERED_NETWORK,
    connect_network,
    create_network,
    remove_network,
)
from harness.hermetic.sidecar import ManagedSidecar, remove_managed_container
from harness.run.types import ProxyConfig

# The pinned base image the packaged proxy runs in — a multi-arch manifest-list
# digest [D005]. Overridable so a CI/other-platform runner can re-pin without a
# code change (the ``VERDI_GRADER_IMAGE`` pattern).
PROXY_BASE_IMAGE = os.environ.get(
    "VERDI_PROXY_IMAGE",
    "python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df",
)

# The managed proxy's container name — resolvable by name on the metered network,
# so a trial's ``HTTP(S)_PROXY=http://<trial-id>@verdi-metering-proxy:3128`` finds
# it. Matches deploy/metering-proxy/docker-compose.yml's ``container_name``.
MANAGED_PROXY_NAME = "verdi-metering-proxy"
PROXY_PORT = 3128
# Reverse-listener ports [RN-11]: allowlist entry i (deterministic order — a
# spec-derived allowlist arrives sorted) is fronted by the reverse listener on
# REVERSE_PORT_BASE + i.
REVERSE_PORT_BASE = 3129
_CONTAINER_LOG = "/var/log/verdi/verdi.jsonl"


class MeteringProxyError(VerdiRefusal, RuntimeError):
    """The managed metering proxy could not be stood up (no daemon, image, or
    the proxy never became ready). Fail loudly — a run that proceeded without a
    working proxy would spend and egress unattributed [PRA-H4]."""


class MeteringProxy(ManagedSidecar):
    """Context manager owning the metered proxy's whole lifecycle [refactor 04 §1].

    A :class:`~harness.hermetic.sidecar.ManagedSidecar` whose divergence is the
    dual network (metered + egress) and the CONNECT allowlist injected into the
    proxy container; the readiness/teardown skeleton is inherited [refactor 11 §G2].
    """

    port = PROXY_PORT
    _ERROR_CLS = MeteringProxyError
    _NOUN = "metering proxy"
    _DAEMON_UNAVAILABLE_MESSAGE = (
        "docker daemon is unavailable — cannot stand up the managed "
        "metering proxy; run without proxy.managed or start docker"
    )
    _LOG_PREFIX = "verdi-metering-"
    _DEFAULT_LOG_BASENAME = "verdi.jsonl"

    def __init__(
        self,
        allow: list[str],
        *,
        log_path: Optional[Path] = None,
        image: str = PROXY_BASE_IMAGE,
        docker: Optional[DockerClient] = None,
        name: str = MANAGED_PROXY_NAME,
    ) -> None:
        self._allow = list(allow)
        self._proxy_src = Path(__file__).resolve().parent / "_proxy_container.py"
        # log-dir/basename resolution: an explicit path is honored as-is (its
        # basename rides into the container via PROXY_LOG so a custom filename is
        # never left touched-but-empty beside verdi.jsonl [P3 interim review F1]);
        # an absent path gets a managed temp dir removed on teardown [refactor 11 §G2].
        super().__init__(log_path=log_path, image=image, docker=docker, name=name)

    @classmethod
    def managed(
        cls, allow: list[str], *, log_path: Optional[Path] = None, image: str = PROXY_BASE_IMAGE
    ) -> "MeteringProxy":
        """Build a managed proxy over ``allow`` (see the class docstring)."""
        return cls(allow, log_path=log_path, image=image)

    # --- divergent seams ------------------------------------------------------
    def _stand_up(self) -> None:
        """Stand up the metered + egress networks and the CONNECT proxy with the
        allowlist **injected** (never a hardcoded set); the proxy alone bridges to
        egress to reach the model APIs [refactor 04 §1]."""
        create_network(self._docker, METERED_NETWORK, internal=True)
        create_network(self._docker, EGRESS_NETWORK, internal=False)
        hc = (
            HardenedCommand()
            .detach()
            .name(self._name)
            .network(METERED_NETWORK)
            .harden()
            .user()
            .env_kv("VERDI_PROXY_ALLOW", ",".join(self._allow))
            .env_kv("PROXY_LOG", f"{os.path.dirname(_CONTAINER_LOG)}/{self._logfile.name}")
        )
        # Reverse listeners [RN-11]: one plain-HTTP terminator per allowlisted host,
        # so a proxy-defiant client (the pinned claude CLI, claude-code#14165) still
        # egresses metered. Only when there is an allowlist — an empty one binds none.
        if self._allow:
            hc.env_kv("VERDI_REVERSE_PORTS", self._reverse_ports_env())
        cmd = (
            hc.volume(self._proxy_src, "/verdi/proxy.py", ro=True)
            .volume(self._logdir, os.path.dirname(_CONTAINER_LOG))
            .image(self._image)
            .arg("python3", "/verdi/proxy.py")
            .build()
        )
        proc = self._docker.run(cmd, timeout_s=120)
        if proc.returncode != 0:
            raise MeteringProxyError(
                f"could not start the metering proxy container: {proc.stderr.strip()}"
            )
        # The proxy (and only the proxy) bridges to egress to reach the model APIs.
        connect_network(self._docker, EGRESS_NETWORK, self._name)

    def _config(self) -> ProxyConfig:
        return ProxyConfig(
            allowlist=list(self._allow),
            proxy_url=f"http://{self._name}:{PROXY_PORT}",
            log_path=str(self._logfile),
            reverse_endpoints=self._reverse_endpoints(),
        )

    def _reverse_ports_env(self) -> str:
        """The ``VERDI_REVERSE_PORTS`` value: allowlist host i fronted by
        ``REVERSE_PORT_BASE + i`` (bare hosts — the proxy defaults each upstream to
        :443) [RN-11]."""
        return ",".join(
            f"{REVERSE_PORT_BASE + i}={host}" for i, host in enumerate(self._allow)
        )

    def _reverse_endpoints(self) -> dict[str, str]:
        """Map each allowlisted host to its in-network reverse listener
        ``http://<ip>:<port>`` (no ``/t`` suffix — the engine appends it per trial) [RN-11].

        The IP is the proxy's METERED_NETWORK address, NOT its name: the pinned bun
        binary bypasses /etc/hosts and container-name DNS is unproven on this network
        (a known intermittent flake), so a name here could strand every claude trial.
        An empty/failed inspect raises loudly — a ProxyConfig with unusable reverse
        endpoints would strand trials with no signal."""
        if not self._allow:
            return {}
        fmt = '{{(index .NetworkSettings.Networks "%s").IPAddress}}' % METERED_NETWORK
        try:
            proc = self._docker.run(
                ["docker", "inspect", "-f", fmt, self._name], timeout_s=30
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise MeteringProxyError(
                f"could not inspect the metering proxy's {METERED_NETWORK} IP: {e} [RN-11]"
            ) from e
        ip = proc.stdout.strip() if proc.returncode == 0 else ""
        if not ip:
            raise MeteringProxyError(
                f"the metering proxy has no {METERED_NETWORK} IP (docker inspect "
                f"returned {proc.stdout!r} / {proc.stderr!r}); its reverse listeners "
                "would be unreachable and every claude trial would strand [RN-11]"
            )
        return {
            host: f"http://{ip}:{REVERSE_PORT_BASE + i}"
            for i, host in enumerate(self._allow)
        }

    def _teardown_networks(self) -> None:
        remove_network(self._docker, EGRESS_NETWORK)
        remove_network(self._docker, METERED_NETWORK)


def teardown_managed(docker: Optional[DockerClient] = None, *, name: str = MANAGED_PROXY_NAME) -> None:
    """Remove the managed proxy container + its networks by their known names.

    The ``bench proxy down`` verb's worker — a standalone teardown that does not
    need the originating :class:`MeteringProxy` object (the names are constants).
    """
    docker = docker or DockerClient()
    remove_managed_container(name, docker)
    remove_network(docker, EGRESS_NETWORK)
    remove_network(docker, METERED_NETWORK)
