"""Shared Docker-availability probe for ``docker``-marked tests.

A single source so the skip semantics stay identical across the suite (they were
copy-pasted into three test files [review #12]).

``VERDI_REQUIRE_DOCKER`` (set on the CI docker job) turns "skip when absent" into
"fail loudly at import": a docker-marked CI job that green-passes by skipping
every test is a false negative. When the variable is set and the daemon is not
reachable, importing this module raises a collection error so the job fails
[XC-1 residual, 7H-2].
"""

from __future__ import annotations

import os
import shutil
import subprocess


class DockerRequiredError(RuntimeError):
    """VERDI_REQUIRE_DOCKER is set but no docker daemon is reachable [7H-2]."""


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


def _resolve_docker_available() -> bool:
    available = docker_available()
    if not available and os.environ.get("VERDI_REQUIRE_DOCKER"):
        raise DockerRequiredError(
            "VERDI_REQUIRE_DOCKER is set but no docker daemon is reachable; the "
            "docker-marked suite must not green-pass by skipping every test. "
            "Provide a daemon or unset VERDI_REQUIRE_DOCKER [7H-2]."
        )
    return available


DOCKER_AVAILABLE = _resolve_docker_available()
