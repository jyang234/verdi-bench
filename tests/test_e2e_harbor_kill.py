"""Real-container kill-on-timeout [PRA-M7].

The fast suite proves RunOutput.kill_failed against a monkeypatched subprocess;
this proves the REAL path against a live daemon: a container that outlives its
timeout is actually killed and reaped, so redaction runs over a static workspace
(RN-10). Uses a `FROM scratch` image built from a statically-linked spin binary,
so it needs no registry pull (image pulls are policy-blocked in some sandboxes) —
just gcc + a docker daemon; it skips if either is absent.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from harness.run.engines.harbor import DockerCliRunner
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_IMAGE = "verdi-spin:e2e"


def _build_spin_image(tmp_path: Path) -> bool:
    """Build a tiny FROM-scratch image whose entrypoint sleeps forever. Returns
    False (⇒ skip) if the toolchain to build it without a registry is absent."""
    gcc = shutil.which("gcc") or shutil.which("cc")
    if not gcc:
        return False
    (tmp_path / "spin.c").write_text(textwrap.dedent("""
        #include <time.h>
        int main(){ struct timespec t={3600,0}; for(;;) nanosleep(&t,0); return 0; }
    """), encoding="utf-8")
    if subprocess.run([gcc, "-static", "-O2", "-o", str(tmp_path / "spin"),
                       str(tmp_path / "spin.c")], capture_output=True).returncode != 0:
        return False
    (tmp_path / "Dockerfile").write_text(
        "FROM scratch\nCOPY spin /spin\nENTRYPOINT [\"/spin\"]\n", encoding="utf-8")
    return subprocess.run(["docker", "build", "-t", _IMAGE, str(tmp_path)],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_m7_real_container_killed_and_reaped_on_timeout(tmp_path):
    if not _build_spin_image(tmp_path):
        pytest.skip("cannot build a registry-free spin image (no gcc / build failed)")
    name = "verdi-spin-e2e-1"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # clean any stale
    out = DockerCliRunner().run_container(
        ["docker", "run", "--rm", "--name", name, _IMAGE], timeout_s=2
    )
    # the timeout fired, the kill/reap was CONFIRMED (not swallowed)...
    assert out.timed_out is True
    assert out.kill_failed is False
    # ...and the container is actually gone, so redaction would see a static tree.
    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    assert name not in ps.stdout
