"""Real-container proof that grader plugins run network-less [PRA-M6].

The fast suite asserts the plugin command carries --network none / --cap-drop;
this proves against a LIVE daemon that a process launched under exactly that
discipline genuinely cannot reach the network — the security property that makes
containerizing plugins meaningful. Uses a FROM-scratch static probe (no registry
pull), so it runs here and in CI; skips without gcc or a daemon.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from harness.grade.container import GradingContainer
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_IMAGE = "verdi-plugin-netprobe:e2e"


def _build_netprobe(tmp_path: Path) -> bool:
    gcc = shutil.which("gcc") or shutil.which("cc")
    if not gcc:
        return False
    (tmp_path / "np.c").write_text(textwrap.dedent("""
        #include <sys/socket.h>
        #include <netinet/in.h>
        #include <arpa/inet.h>
        int main(){
          int s=socket(AF_INET,SOCK_STREAM,0); if(s<0) return 3;
          struct sockaddr_in a; a.sin_family=AF_INET; a.sin_port=htons(53);
          inet_pton(AF_INET,"1.1.1.1",&a.sin_addr);
          return connect(s,(struct sockaddr*)&a,sizeof(a))==0 ? 0 : 2;
        }
    """), encoding="utf-8")
    if subprocess.run([gcc, "-static", "-O2", "-o", str(tmp_path / "np"),
                       str(tmp_path / "np.c")], capture_output=True).returncode != 0:
        return False
    (tmp_path / "Dockerfile").write_text(
        "FROM scratch\nCOPY np /np\nENTRYPOINT [\"/np\"]\n", encoding="utf-8")
    return subprocess.run(["docker", "build", "-t", _IMAGE, str(tmp_path)],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_m6_plugin_container_has_no_network(tmp_path):
    if not _build_netprobe(tmp_path):
        pytest.skip("cannot build a registry-free netprobe image (no gcc / build failed)")
    # Run the probe under the EXACT network/cap flags the plugin runner uses
    # (build_plugin_command), substituting the probe as the image + entrypoint.
    gc = GradingContainer(image=_IMAGE)
    cmd = gc.build_plugin_command(tmp_path, [])
    # strip the python plugin entrypoint (the scratch probe IS the entrypoint) but
    # keep the isolation flags — this is what a real plugin container inherits.
    flags = cmd[: cmd.index(_IMAGE) + 1]
    assert "--network" in flags and flags[flags.index("--network") + 1] == "none"
    proc = subprocess.run(flags, capture_output=True)
    assert proc.returncode == 2, (
        f"expected the network-less plugin container to be BLOCKED (exit 2), got "
        f"{proc.returncode} — a plugin could reach the network"
    )
