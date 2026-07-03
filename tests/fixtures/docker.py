"""Shared Docker-availability probe for ``docker``-marked tests.

A single source so the skip semantics stay identical across the suite (they were
copy-pasted into three test files [review #12]).
"""

from __future__ import annotations

import shutil
import subprocess


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


DOCKER_AVAILABLE = docker_available()
