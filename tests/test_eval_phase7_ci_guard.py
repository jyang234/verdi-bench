"""7H-2 / XC-1 — the docker-marked CI job cannot green-pass by skipping.

VERDI_REQUIRE_DOCKER turns the fixture's "skip when the daemon is absent" into a
loud import-time failure, so a daemon-less docker job fails at collection rather
than reporting all-green from all-skipped.
"""

from __future__ import annotations

import pytest

import tests.fixtures.docker as docker_mod


def test_require_docker_raises_when_daemon_absent(monkeypatch):
    monkeypatch.setenv("VERDI_REQUIRE_DOCKER", "1")
    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)
    with pytest.raises(docker_mod.DockerRequiredError):
        docker_mod._resolve_docker_available()


def test_require_docker_silent_when_daemon_present(monkeypatch):
    monkeypatch.setenv("VERDI_REQUIRE_DOCKER", "1")
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    assert docker_mod._resolve_docker_available() is True


def test_without_require_docker_absence_is_a_plain_skip(monkeypatch):
    monkeypatch.delenv("VERDI_REQUIRE_DOCKER", raising=False)
    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)
    # no raise — the suite simply skips docker-marked tests locally
    assert docker_mod._resolve_docker_available() is False
