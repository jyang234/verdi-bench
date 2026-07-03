"""Phase-2 exit: a docker-marked end-to-end Harbor trial [XC-1].

``test_docker_harbor_trial_end_to_end`` is the first test to run a *subject*
container through the real Harbor engine (Phase 1's docker test ran only a
*grader* container). It proves, against a real daemon, the real execution path:
the container reads its /verdi/request.json (prompt + arm), a provider key
injected as env is echoed by the container and then redacted from the persisted
artifact, the image Id lands in provenance, and the record is ADVISORY.

It is skipped where no daemon is present and runs in the labelled ``docker`` CI
job. The metering-proxy *mechanism* (per-trial JSONL attribution, network
creation, kill-on-timeout) is unit-proven in test_eval4_harbor_egress.py; a full
real-proxy egress e2e is intentionally out of this test.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from harness.adapters.base import ADVISORY
from harness.run.engines.harbor import HarborEngine
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


DOCKER_AVAILABLE = _docker_available()


@pytest.mark.docker
@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_docker_harbor_trial_end_to_end(tmp_path):
    ctx = tmp_path / "img"
    ctx.mkdir()
    # The trial image reads its request and echoes the injected key into the
    # workspace (the key must NOT survive redaction). No shell-injected secrets:
    # the key arrives via the environment.
    (ctx / "Dockerfile").write_text(
        "FROM busybox\n"
        'CMD ["sh", "-c", "mkdir -p /workspace/artifacts && '
        "cp /verdi/request.json /workspace/artifacts/req.json && "
        'echo \\"key=$CORP_TOKEN\\" > /workspace/artifacts/leak.txt"]\n',
        encoding="utf-8",
    )
    image = "verdi-bench/harbor-e2e:latest"
    subprocess.run(["docker", "build", "-t", image, str(ctx)], check=True, capture_output=True)

    secret = "corp-internal-token-a1b2c3"  # a shape no _SECRET_PATTERNS catches
    arm = Arm(name="control", platform="claude_code",
              model="anthropic/claude-3-5-sonnet-20241022", payload={"temperature": 0})
    ws = tmp_path / "ws"
    rec = run_trial(
        Task(id="t", prompt="solve X", image=image), arm, ws,
        RunConfig(engine=HarborEngine(), provider_keys={"CORP_TOKEN": secret}),
    )

    # the container saw its prompt + arm through the read-only request mount
    req = json.loads((ws / "artifacts" / "req.json").read_text(encoding="utf-8"))
    assert req["prompt"] == "solve X" and req["arm"] == "control"

    # the injected key was redacted from the persisted artifact [AC-8, RN-9]
    leak = (ws / "artifacts" / "leak.txt").read_text(encoding="utf-8")
    assert secret not in leak
    assert "[REDACTED]" in leak

    # provenance: a real image digest (the local image Id) and ADVISORY tier
    assert rec.provenance.image_digest and rec.provenance.image_digest.startswith("sha256:")
    assert rec.provenance.tier == ADVISORY
    assert rec.outcome.value == "completed"
