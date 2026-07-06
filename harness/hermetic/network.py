"""Metered + egress docker networks for hermetic trials [refactor 04 §1].

:data:`METERED_NETWORK` is **THE** constant: harbor's ``--network`` flag, the
``deploy/metering-proxy`` docker-compose file, and the shakedown scripts all bind
to this exact string. It never changes — a versioned invariant [refactor 04 §6] —
which is why it lives here once instead of being restated (the
``harbor_multiagent.py`` "MUST match harbor's constant" comment was the smell).
"""

from __future__ import annotations

import subprocess

# Absolute import (not ``from .docker import ...``): the AST seam sweep
# (tests/test_eval4_seam.py) flags a bare module name ``docker`` in an import.
from harness.hermetic.docker import DockerClient

# The internal network a proxied trial joins — ``--internal`` gives it no route
# except the metering proxy attached to it [RN-11, D001].
METERED_NETWORK = "verdi-metered"
# The proxy's (and only the proxy's) route out to the model APIs. Matches the
# ``verdi-egress`` name in deploy/metering-proxy/docker-compose.yml.
EGRESS_NETWORK = "verdi-egress"


def ensure_metered_network(docker: DockerClient) -> None:
    """Create the restricted (``--internal``) metering network if it is absent [RN-11].

    Best-effort: an unreachable daemon returns quietly — the trial itself then
    fails closed as a ``daemon_error``, so this never masks that (the exact
    pre-refactor ``DockerCliRunner.ensure_metered_network`` behavior).
    """
    try:
        inspect = docker.run(["docker", "network", "inspect", METERED_NETWORK], timeout_s=30)
        if inspect.returncode == 0:
            return
        docker.run(["docker", "network", "create", "--internal", METERED_NETWORK], timeout_s=30)
    except (OSError, subprocess.SubprocessError):
        return


def create_network(docker: DockerClient, name: str, *, internal: bool = False) -> None:
    """Create ``name`` (idempotent — an already-present network is left as-is).

    Unlike :func:`ensure_metered_network` this raises loudly if creation truly
    fails, because the managed proxy cannot function without its networks.
    """
    inspect = docker.run(["docker", "network", "inspect", name], timeout_s=30)
    if inspect.returncode == 0:
        return
    argv = ["docker", "network", "create"]
    if internal:
        argv.append("--internal")
    argv.append(name)
    proc = docker.run(argv, timeout_s=30)
    if proc.returncode != 0:
        raise NetworkError(f"could not create docker network {name!r}: {proc.stderr.strip()}")


def connect_network(
    docker: DockerClient, name: str, container: str, *, aliases: tuple[str, ...] = ()
) -> None:
    """Attach ``container`` to ``name`` (optionally with DNS aliases)."""
    argv = ["docker", "network", "connect"]
    for alias in aliases:
        argv += ["--alias", alias]
    argv += [name, container]
    proc = docker.run(argv, timeout_s=30)
    if proc.returncode != 0:
        raise NetworkError(
            f"could not connect {container!r} to network {name!r}: {proc.stderr.strip()}"
        )


def remove_network(docker: DockerClient, name: str) -> None:
    """Remove ``name``; best-effort (a still-attached container or an absent
    network must not turn teardown into a crash)."""
    try:
        docker.run(["docker", "network", "rm", name], timeout_s=30)
    except (OSError, subprocess.SubprocessError):
        return


class NetworkError(RuntimeError):
    """A docker network create/connect the managed proxy depends on failed."""
