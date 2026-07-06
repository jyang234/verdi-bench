"""EVAL-4 RN-4/D-8 — the trial-image contract: prompt + arm config reach the
container through a read-only /verdi/request.json mount, outside the workspace.

The unit tests drive the seam with a fake docker runner (no daemon). The
docker-marked test proves a REAL container reads its request; it is skipped
where no daemon is present and runs in a labelled/scheduled CI job.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness.run.engines.harbor import TRIAL_REQUEST_MOUNT, HarborEngine
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.run_fakes import FakeDockerRunner


def _arm(**kw):
    base = dict(name="control", platform="claude_code",
                model="anthropic/claude-3-5-sonnet-20241022")
    base.update(kw)
    return Arm(**base)


def _pinned_task():
    return Task(id="t", prompt="solve X", image="verdi-bench/agent@sha256:" + "a" * 64)


class _CapturingRunner(FakeDockerRunner):
    """Reads the /verdi/request.json the harness mounts, during the run."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.request_payload = None
        self.request_host_path: Path | None = None

    def run_container(self, cmd, timeout_s, env=None):
        for i, tok in enumerate(cmd):
            if tok == "--volume" and cmd[i + 1].endswith(f":{TRIAL_REQUEST_MOUNT}:ro"):
                host = cmd[i + 1].split(":")[0]
                self.request_host_path = Path(host)
                self.request_payload = json.loads(Path(host).read_text(encoding="utf-8"))
        return super().run_container(cmd, timeout_s, env)


def test_rn17_corrupt_telemetry_fails_trial_closed(tmp_path):
    """RN-17: a present-but-corrupt agent_log.json must fail the trial closed as
    infra_failed(telemetry_corrupt), not silently become 'no telemetry'."""
    from harness.adapters.base import Outcome

    runner = FakeDockerRunner(corrupt_log=True)
    rec = run_trial(_pinned_task(), _arm(), tmp_path / "ws",
                    RunConfig(engine=HarborEngine(runner=runner)))
    assert rec.outcome == Outcome.infra_failed
    assert rec.flags.failure_reason == "telemetry_corrupt"


def test_rn17_absent_telemetry_is_legitimate(tmp_path):
    """An absent log is legitimate (the arm may emit none) — it reads as no
    telemetry and the trial still completes; only corruption fails closed."""
    from harness.adapters.base import Outcome

    class _NoLogRunner(FakeDockerRunner):
        def run_container(self, cmd, timeout_s, env=None):
            out = super().run_container(cmd, timeout_s, env)
            # remove the log the base fake wrote, simulating an arm that emits none
            for i, tok in enumerate(cmd):
                if tok == "--volume" and ":/workspace" in cmd[i + 1]:
                    host = cmd[i + 1].split(":")[0]
                    (Path(host) / "artifacts" / "agent_log.json").unlink(missing_ok=True)
            return out

    rec = run_trial(_pinned_task(), _arm(), tmp_path / "ws",
                    RunConfig(engine=HarborEngine(runner=_NoLogRunner())))
    assert rec.outcome == Outcome.completed
    assert getattr(rec.flags, "failure_reason", None) is None


def test_ac1_trial_request_delivered_readonly(tmp_path):
    """RN-4/D-8: prompt + arm (name, model, payload) reach the container."""
    arm = _arm(model="anthropic/claude-3-5-sonnet-20241022", payload={"temperature": 0})
    runner = _CapturingRunner(native_log={})
    run_trial(_pinned_task(), arm, tmp_path / "ws", RunConfig(engine=HarborEngine(runner=runner)))
    p = runner.request_payload
    assert p is not None, "no /verdi/request.json was mounted"
    assert p["prompt"] == "solve X"
    assert p["arm"] == "control"
    assert p["model"] == "anthropic/claude-3-5-sonnet-20241022"
    assert p["payload"] == {"temperature": 0}
    # A1: the request file carries a schema_version (additive; the keys above are
    # unchanged), so a future consumer can branch on the version.
    from harness.run.request import TRIAL_REQUEST_SCHEMA_VERSION

    assert p["schema_version"] == TRIAL_REQUEST_SCHEMA_VERSION == 1


def test_trial_request_outside_workspace_and_cleaned(tmp_path):
    """D-8: the request lives OUTSIDE the workspace (so it never pollutes the
    graded copy) and the host temp file is cleaned up after the trial."""
    ws = tmp_path / "ws"
    runner = _CapturingRunner(native_log={})
    run_trial(_pinned_task(), _arm(), ws, RunConfig(engine=HarborEngine(runner=runner)))
    req = runner.request_host_path
    assert req is not None
    assert ws.resolve() not in req.resolve().parents  # not under the workspace
    assert not req.exists()  # cleaned up


# --- docker-marked: a REAL container reads its request ---------------------
from tests.fixtures.docker import DOCKER_AVAILABLE  # noqa: E402


@pytest.mark.docker
@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_docker_trial_reads_its_request(tmp_path):
    """A real container reads /verdi/request.json and its prompt/arm land in an
    artifact — proving the harness↔image contract end to end (RN-4/D-8)."""
    ctx = tmp_path / "img"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        "FROM busybox\n"
        # copy the mounted request into the workspace artifacts so the harness
        # can read back what the container saw
        'CMD ["sh", "-c", "mkdir -p /workspace/artifacts && '
        'cp /verdi/request.json /workspace/artifacts/seen_request.json"]\n',
        encoding="utf-8",
    )
    image = "verdi-bench/trial-req-e2e:latest"
    subprocess.run(["docker", "build", "-t", image, str(ctx)], check=True, capture_output=True)

    ws = tmp_path / "ws"
    ws.mkdir()
    arm = _arm(payload={"temperature": 0})
    # reference by tag: --pull=never runs the local image, and resolve_digest
    # pins provenance to its image Id (the local-image fallback).
    rec = run_trial(
        Task(id="t", prompt="solve X", image=image), arm, ws, RunConfig(engine=HarborEngine()),
    )
    seen = json.loads((ws / "artifacts" / "seen_request.json").read_text(encoding="utf-8"))
    assert seen["prompt"] == "solve X"
    assert seen["arm"] == "control"
    # A1: a REAL container reads the versioned request file end to end.
    assert seen["schema_version"] == 1
    assert rec.provenance.image_digest and rec.provenance.image_digest.startswith("sha256:")
