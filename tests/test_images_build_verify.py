"""``harness.images`` — build/pin/verify/registry behavior [refactor 03 §4].

The unit tests drive build/verify through a FAKE DockerClient (no daemon): the
fake simulates ``docker build`` / ``docker inspect`` and, for a verify run,
"executes" the image by writing whatever log the scenario scripts into the mounted
workspace. This exercises the real check logic deterministically. The
``docker``-marked tests at the bottom prove a REAL build + verify end to end,
including the loud rejection of a non-compliant image.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from harness.images.build import (
    ImageBuildError,
    ImageResolveError,
    build,
    resolve_digest,
)
from harness.images.registry import (
    UnknownImageError,
    official_names,
    resolve,
)
from harness.images.spec import EnvironmentSpec, ImageSpec, PinnedImage
from harness.images.verify import verify

_LOCAL_ID = "sha256:" + "a" * 64
_REPO_DIGEST = "sha256:" + "b" * 64


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeDockerClient:
    """A no-daemon stand-in for ``harness.hermetic.DockerClient`` [refactor 03 §4].

    ``build_ok`` toggles a build failure; ``repo_digest`` supplies a RepoDigest (a
    pushed registry image) else the pin falls back to the local ``.Id``. For a
    container RUN (verify), ``agent_log`` is written into the mounted workspace to
    simulate the agent, ``exit_status`` is the container exit, and ``timeout``
    raises like a blown ``--timeout``.
    """

    def __init__(
        self,
        *,
        build_ok=True,
        repo_digest=None,
        local_id=_LOCAL_ID,
        agent_log=None,
        write_log_text=None,
        exit_status=1,
        timeout=False,
        run_stderr="",
    ):
        self.build_ok = build_ok
        self.repo_digest = repo_digest
        self.local_id = local_id
        self.agent_log = agent_log
        self.write_log_text = write_log_text
        self.exit_status = exit_status
        self.timeout = timeout
        self.run_stderr = run_stderr
        self.build_calls: list[list[str]] = []

    def run(self, argv, *, timeout_s=None, env=None, text=True):
        if argv[:2] == ["docker", "build"]:
            self.build_calls.append(argv)
            return _cp(0 if self.build_ok else 1, stderr="" if self.build_ok else "build boom")
        if argv[:2] == ["docker", "inspect"]:
            fmt = argv[argv.index("--format") + 1]
            if "RepoDigests" in fmt:
                if self.repo_digest is None:
                    return _cp(1)  # no RepoDigest — pin falls back to .Id
                ref = argv[-1]
                repo = ref.split(":")[0].split("@")[0]
                return _cp(0, stdout=f"{repo}@{self.repo_digest}")
            if ".Id" in fmt:
                return _cp(0, stdout=self.local_id) if self.local_id else _cp(1)
            return _cp(1)
        # a container RUN (verify): simulate the agent writing into /workspace.
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd="docker run", timeout=timeout_s or 0)
        ws = _workspace_host(argv)
        if ws is not None and (self.agent_log is not None or self.write_log_text is not None):
            art = ws / "artifacts"
            art.mkdir(parents=True, exist_ok=True)
            text_out = (
                self.write_log_text
                if self.write_log_text is not None
                else json.dumps(self.agent_log)
            )
            (art / "agent_log.json").write_text(text_out, encoding="utf-8")
        return _cp(self.exit_status, stderr=self.run_stderr)


def _workspace_host(argv) -> Path | None:
    for i, tok in enumerate(argv):
        if tok == "--volume" and i + 1 < len(argv) and argv[i + 1].endswith(":/workspace"):
            return Path(argv[i + 1].rsplit(":", 1)[0])
    return None


# --- spec models -----------------------------------------------------------
def test_imagespec_forbids_unknown_key():
    with pytest.raises(Exception):
        ImageSpec(ref="x", bogus=1)


def test_environmentspec_defaults_and_forbids_unknown():
    e = EnvironmentSpec()
    assert e.files == {} and e.env == {} and e.extra_hosts == []
    with pytest.raises(Exception):
        EnvironmentSpec(secret_key="nope")


# --- build / pin -----------------------------------------------------------
def test_build_pins_local_image_to_id(tmp_path):
    ctx = tmp_path / "img"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    fake = FakeDockerClient()
    pinned = build(ImageSpec(ref="verdi-local/x:latest", build_context=ctx), docker=fake)
    assert isinstance(pinned, PinnedImage)
    assert pinned.digest == _LOCAL_ID
    assert pinned.pinned_ref == _LOCAL_ID
    assert fake.build_calls, "docker build was never invoked"


def test_build_prefers_repo_digest_when_pushed(tmp_path):
    ctx = tmp_path / "img"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    fake = FakeDockerClient(repo_digest=_REPO_DIGEST)
    pinned = build(ImageSpec(ref="repo/x:latest", build_context=ctx), docker=fake)
    assert pinned.digest == _REPO_DIGEST
    assert pinned.pinned_ref == f"repo/x@{_REPO_DIGEST}"


def test_build_fails_loudly_on_docker_error(tmp_path):
    ctx = tmp_path / "img"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(ImageBuildError):
        build(ImageSpec(ref="x", build_context=ctx), docker=FakeDockerClient(build_ok=False))


def test_build_extends_base_builds_base_first(tmp_path):
    ctx = tmp_path / "img"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("FROM verdi-base\nCOPY agent.py /agent.py\n", encoding="utf-8")
    fake = FakeDockerClient()
    build(ImageSpec(ref="verdi-local/x:latest", build_context=ctx), docker=fake)
    # two builds: verdi-base first, then the target
    tags = [c[c.index("-t") + 1] for c in fake.build_calls]
    assert tags == ["verdi-base", "verdi-local/x:latest"]


def test_resolve_digest_absent_raises():
    with pytest.raises(ImageResolveError):
        resolve_digest("ghost:latest", docker=FakeDockerClient(local_id=None))


def test_resolve_digest_passthrough_pinned_ref():
    ref = f"repo/x@{_REPO_DIGEST}"
    assert resolve_digest(ref, docker=FakeDockerClient()) == _REPO_DIGEST


# --- verify ----------------------------------------------------------------
_GOOD_LOG = {
    "verdi_log_version": 1,
    "telemetry": {"tokens_in": 10, "tokens_out": 5},
    "trajectory": [{"kind": "file_edit", "files_touched": ["solution.py"]}],
}


def test_verify_compliant_generic():
    report = verify("img", docker=FakeDockerClient(agent_log=_GOOD_LOG, exit_status=1))
    assert report.ok, [c.model_dump() for c in report.checks if not c.ok]
    assert {c.name for c in report.checks} == {
        "image_ran", "wrote_agent_log", "log_parses", "exit_semantics", "no_writes_outside_workspace",
    }


def test_verify_missing_log_is_named_failure():
    report = verify("img", docker=FakeDockerClient(agent_log=None, exit_status=0))
    assert not report.ok
    assert report.first_failure.name == "wrote_agent_log"


def test_verify_corrupt_json_fails_at_parse():
    report = verify("img", docker=FakeDockerClient(write_log_text="{not json", exit_status=1))
    assert not report.ok
    assert report.first_failure.name == "log_parses"


def test_verify_declared_but_malformed_generic_fails_loud():
    # a declared v1 log with a typo'd block name must be refused (GenericLogError)
    bad = {"verdi_log_version": 1, "telemetrie": {"tokens_in": 1}}
    report = verify("img", docker=FakeDockerClient(agent_log=bad, exit_status=0))
    assert not report.ok
    assert report.first_failure.name == "log_parses"
    assert "GenericLogError" in report.first_failure.detail


def test_verify_timeout_is_named_failure():
    report = verify("img", docker=FakeDockerClient(timeout=True), timeout_s=1)
    assert not report.ok
    assert report.first_failure.name == "image_ran"
    assert "timed out" in report.first_failure.detail


def test_verify_daemon_error_fails_image_ran():
    report = verify("img", docker=FakeDockerClient(agent_log=_GOOD_LOG, exit_status=125))
    assert not report.ok
    assert report.first_failure.name == "image_ran"


def test_verify_native_requires_platform():
    report = verify("img", expected_format="native", docker=FakeDockerClient())
    assert not report.ok
    assert report.first_failure.name == "declared_format"


def test_verify_reserved_exit_code_flagged():
    # an agent that exits 124 usurps the runner's timeout code
    report = verify("img", docker=FakeDockerClient(agent_log=_GOOD_LOG, exit_status=124))
    names = {c.name for c in report.checks if not c.ok}
    assert "exit_semantics" in names


# --- registry --------------------------------------------------------------
def test_registry_lists_official_images():
    names = official_names()
    assert {"base", "generic-llm", "anthropic-claude-code", "openai-codex"} <= set(names)


def test_registry_resolves_official_name():
    spec = resolve("generic-llm")
    assert spec.build_context is not None and spec.build_context.name == "generic-llm"
    assert spec.expected_format == "generic"


def test_registry_resolves_context_path(tmp_path):
    ctx = tmp_path / "myimg"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    spec = resolve(str(ctx))
    assert spec.build_context == ctx
    assert spec.ref == "verdi-local/myimg:latest"


def test_registry_unknown_target_raises():
    with pytest.raises(UnknownImageError):
        resolve("no-such-image-or-path")


# --- source-level contract: the tunnel lives in exactly one place ----------
_IMAGES_ROOT = Path(__file__).resolve().parents[1] / "images"
# The hand-rolled metering-proxy dance is characterized IN CODE by http.client's
# CONNECT primitive (``set_tunnel``) and the credential header set as a string
# literal (``"Proxy-Authorization"``). Prose that merely NAMES the header (a
# docstring or a README compliance table) is not the sequence and is not scanned.
_TUNNEL_SIGNATURE = re.compile(r"""set_tunnel|['"]Proxy-Authorization['"]""")


def test_only_verdi_base_owns_the_proxy_tunnel():
    """The CONNECT-tunnel + Proxy-Authorization dance the metering proxy requires
    is owned by ``verdi_agent.post_json`` (images/base) and nowhere else: a trial
    image ``import``s it, never re-implements it [refactor 03 §2, G1]. This sweep
    is the forcing function — if any image agent outside images/base/ hand-rolls
    the tunnel again, it fails loudly here instead of silently duplicating the seam.
    """
    base = _IMAGES_ROOT / "base"
    offenders = sorted(
        str(py.relative_to(_IMAGES_ROOT))
        for py in _IMAGES_ROOT.rglob("*.py")
        if base not in py.parents
        and _TUNNEL_SIGNATURE.search(py.read_text(encoding="utf-8"))
    )
    assert not offenders, (
        "hand-rolled proxy tunnel (set_tunnel / \"Proxy-Authorization\") found "
        f"outside images/base/ — import verdi_agent.post_json instead: {offenders}"
    )


def test_the_tunnel_sweep_actually_bites():
    """The sweep is a real guard, not vacuous: the exact dance verdi_agent owns,
    dropped into a file OUTSIDE images/base/, is caught by the signature."""
    hand_rolled = (
        "conn = http.client.HTTPSConnection(pu.hostname, pu.port or 3128)\n"
        '        tunnel_headers = {"Proxy-Authorization": "Basic " + cred}\n'
        "        conn.set_tunnel(host, 443, headers=tunnel_headers)\n"
    )
    assert _TUNNEL_SIGNATURE.search(hand_rolled)
    # and verdi_agent itself (the one legitimate home) carries the signature —
    # proof the sweep would fire on it were images/base/ not exempted.
    assert _TUNNEL_SIGNATURE.search(
        (_IMAGES_ROOT / "base" / "verdi_agent.py").read_text(encoding="utf-8")
    )


# --- docker-marked: a REAL build + verify ----------------------------------
from tests.fixtures.docker import DOCKER_AVAILABLE  # noqa: E402


@pytest.mark.docker
@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_docker_build_and_verify_generic_llm():
    """`bench images build generic-llm` pins a real image and `verify` PASSES it —
    the offline compliance check with no keys and --network none [refactor 03 §4]."""
    pinned = build(resolve("generic-llm"))
    assert pinned.digest.startswith("sha256:")
    report = verify(pinned.pinned_ref)
    assert report.ok, [c.model_dump() for c in report.checks if not c.ok]


@pytest.mark.docker
@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_docker_verify_rejects_noncompliant_image():
    """A plain python image writes no agent_log.json — `verify` FAILS loudly with a
    named reason (wrote_agent_log), never a silent pass [refactor 03 §4]."""
    from harness.hermetic import DockerClient

    DockerClient().run(["docker", "pull", "python:alpine"], timeout_s=180)
    report = verify("python:alpine")
    assert not report.ok
    assert report.first_failure.name == "wrote_agent_log"
