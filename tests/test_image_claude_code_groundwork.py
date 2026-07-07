"""The payload-gated ``claude-code-groundwork`` trial image [integration plan §4, A2].

The unit tests import the image's PURE gating functions (loaded the same way the
container does — with ``images/base`` on ``sys.path`` so ``import verdi_agent``
resolves) and prove, WITHOUT a container:

* the payload gating truth table (enabled / disabled / absent payload / a ``tools``
  list without ``groundwork`` / a non-list ``tools`` — fail-closed);
* the generated MCP config content + that every launch path is absolute;
* the "nothing under ``/workspace`` except ``artifacts/``" rule — both declaratively
  (the plan's write destinations) and by applying the plan against tmp fake
  home + workspace dirs;
* the control path writes nothing and reproduces the shipped official CLI argv;
* the treatment argv is the control argv + EXACTLY the ``--mcp-config`` addition.

The ``docker``-marked smoke test at the bottom builds the real image (prebuilt
fallback) and proves the baked binaries run, the skill is staged, the CLI is
installed, and arming vs control behaves correctly end to end.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_IMG = _ROOT / "images" / "reference" / "claude-code-groundwork"
_AGENT = _IMG / "agent.py"
_OFFICIAL_AGENT = _ROOT / "images" / "official" / "anthropic-claude-code" / "agent.py"
# The rebased agents `import verdi_agent` (the stdlib-only in-image SDK at
# images/base, on the image's PYTHONPATH=/opt/verdi). Put that dir on sys.path so
# exec_module resolves the import here, mirroring the container [refactor 03 §3].
_BASE = _ROOT / "images" / "base"

# The shipped official invocation this image forks; the control arm must reproduce
# it byte-for-byte. Kept here so a drift in either agent's CLI fails a test.
_SHIPPED_CLI = ["claude", "--print", "--permission-mode", "acceptEdits"]
_TREATMENT = {"tools": ["groundwork"], "workflow": "ground_verify"}


def _load(module_name: str, path: Path):
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the frozen dataclass (under `from __future__ import
    # annotations`) can resolve its own module namespace on 3.11 — the container
    # runs agent.py as __main__, which is always in sys.modules; a dynamic load is
    # not, so we add it here.
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # import-safe: main() is guarded, gating fns pure
    return mod


agent = _load("_ccg_agent", _AGENT)


# --- payload gating truth table --------------------------------------------
def test_groundwork_enabled_truth_table():
    on = agent.groundwork_enabled
    # enabled: groundwork present in a tools list (with or without siblings)
    assert on({"tools": ["groundwork"]}) is True
    assert on(_TREATMENT) is True
    assert on({"tools": ["grep", "groundwork", "read"]}) is True
    # disabled: absent payload, no tools key, empty/other tools
    assert on({}) is False
    assert on({"workflow": "ground_verify"}) is False
    assert on({"tools": []}) is False
    assert on({"tools": ["grep", "read"]}) is False
    # fail-closed: a non-list `tools` (e.g. the bare string) never arms, even
    # though "groundwork" in "groundwork" is truthy for a str.
    assert on({"tools": "groundwork"}) is False
    assert on({"tools": None}) is False


def test_plan_disabled_is_the_empty_plan():
    plan = agent.plan_groundwork({})
    assert plan.enabled is False
    assert plan.symlinks == () and plan.files == () and plan.copies == ()
    assert plan.mkdirs == () and plan.cli_extra_args == ()
    assert plan.path_bin_dir == "" and plan.mcp_config_path == ""


def test_plan_enabled_has_every_arming_step():
    plan = agent.plan_groundwork(_TREATMENT)
    assert plan.enabled is True
    assert len(plan.symlinks) == 2  # flowmap + groundwork
    assert plan.path_bin_dir and len(plan.files) == 1 and len(plan.copies) == 1
    assert plan.cli_extra_args[0] == "--mcp-config"


# --- MCP config content + absolute paths -----------------------------------
def test_mcp_config_content_and_absolute_paths():
    # real defaults ($HOME=/tmp, workspace=/workspace, staging=/opt/groundwork)
    plan = agent.plan_groundwork(_TREATMENT)
    (config_path, config_text), = plan.files
    # the config file is written under $HOME, never /workspace
    assert config_path == "/tmp/groundwork.mcp.json"
    cfg = json.loads(config_text)
    server = cfg["mcpServers"]["groundwork"]
    assert server["type"] == "stdio"
    assert server["command"] == "/opt/groundwork/bin/groundwork"
    # the exact launch contract of cmd/groundwork/mcp.go (integration plan §4)
    assert server["args"] == [
        "mcp",
        "/workspace/graph.json",
        "--policy",
        "/workspace/policy.json",
        "--log",
        "/workspace/artifacts/groundwork-mcp.jsonl",
    ]
    # every path the launch names is absolute
    for tok in (server["command"], *(a for a in server["args"] if a.startswith("/"))):
        assert os.path.isabs(tok), tok
    # the --mcp-config arg points at the written config, by ABSOLUTE path
    assert plan.cli_extra_args == ("--mcp-config", "/tmp/groundwork.mcp.json")
    assert os.path.isabs(plan.cli_extra_args[1])
    assert plan.mcp_config_path == config_path


def test_mcp_config_render_is_byte_stable():
    """The config is a pure function of its inputs — no run-to-run variation."""
    make = lambda: agent.render_mcp_config(  # noqa: E731
        agent.mcp_server_config(
            groundwork_bin="/opt/groundwork/bin/groundwork",
            graph="/workspace/graph.json",
            policy="/workspace/policy.json",
            log="/workspace/artifacts/groundwork-mcp.jsonl",
        )
    )
    assert make() == make()
    assert make().endswith("\n")


# --- the /workspace rule: declarative + applied ----------------------------
def test_plan_declares_no_workspace_write_except_artifacts():
    """Every filesystem destination the plan will touch is under $HOME, EXCEPT the
    single ``/workspace/artifacts`` mkdir (the judge-excluded MCP-log dir, D7)."""
    ws, home = "/workspace", "/tmp"
    plan = agent.plan_groundwork(_TREATMENT, home=home, workspace=ws)
    dests = [
        *plan.mkdirs,
        *(link for _, link in plan.symlinks),
        *(path for path, _ in plan.files),
        *(dst for _, dst in plan.copies),
    ]
    under_ws = [d for d in dests if d == ws or d.startswith(ws + os.sep)]
    assert under_ws == [os.path.join(ws, "artifacts")], under_ws
    # the plan names that one workspace destination explicitly
    assert plan.artifacts_dir == os.path.join(ws, "artifacts")
    # and the symlink TARGETS (reads, not writes) point at the off-PATH staging dir
    assert all(t.startswith("/opt/groundwork/bin/") for t, _ in plan.symlinks)


def test_apply_writes_only_artifacts_under_workspace(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    staging_bin = tmp_path / "opt" / "bin"
    staging_skill = tmp_path / "opt" / "skills" / "groundwork-workflow"
    for d in (home, ws, staging_bin, staging_skill):
        d.mkdir(parents=True)
    # fake staged sources so the symlinks + skill copy have real targets
    (staging_bin / "flowmap").write_text("#!/bin/true\n", encoding="utf-8")
    (staging_bin / "groundwork").write_text("#!/bin/true\n", encoding="utf-8")
    (staging_skill / "SKILL.md").write_text(
        "---\nname: groundwork-workflow\n---\n", encoding="utf-8"
    )

    plan = agent.plan_groundwork(
        _TREATMENT,
        home=str(home),
        workspace=str(ws),
        staging_bin=str(staging_bin),
        staging_skill=str(staging_skill),
    )
    agent.apply_plan(plan)

    # /workspace: ONLY artifacts/ exists (recursively), and it's an empty dir the
    # MCP server will populate — no groundwork-branded file loose in the diff.
    assert {str(p.relative_to(ws)) for p in ws.rglob("*")} == {"artifacts"}
    assert (ws / "artifacts").is_dir()

    # $HOME carries the config + skill + the on-PATH symlinks
    assert (home / "groundwork.mcp.json").is_file()
    assert (home / ".claude" / "skills" / "groundwork-workflow" / "SKILL.md").is_file()
    for b in ("flowmap", "groundwork"):
        link = home / ".local" / "bin" / b
        assert link.is_symlink()
        assert os.readlink(link) == str(staging_bin / b)


def test_apply_is_idempotent(tmp_path):
    """Re-arming (a re-run) replaces links/copies without error."""
    home, ws = tmp_path / "home", tmp_path / "ws"
    staging_bin = tmp_path / "opt" / "bin"
    staging_skill = tmp_path / "opt" / "skills" / "groundwork-workflow"
    for d in (home, ws, staging_bin, staging_skill):
        d.mkdir(parents=True)
    (staging_bin / "flowmap").write_text("x", encoding="utf-8")
    (staging_bin / "groundwork").write_text("x", encoding="utf-8")
    (staging_skill / "SKILL.md").write_text("---\nname: groundwork-workflow\n---\n", encoding="utf-8")
    plan = agent.plan_groundwork(
        _TREATMENT, home=str(home), workspace=str(ws),
        staging_bin=str(staging_bin), staging_skill=str(staging_skill),
    )
    agent.apply_plan(plan)
    agent.apply_plan(plan)  # must not raise
    assert (home / ".claude" / "skills" / "groundwork-workflow" / "SKILL.md").is_file()


# --- control: zero writes + the shipped argv/env ---------------------------
def test_control_apply_writes_nothing(tmp_path):
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    plan = agent.plan_groundwork({}, home=str(home), workspace=str(ws))
    agent.apply_plan(plan)
    assert list(home.iterdir()) == []
    assert list(ws.iterdir()) == []


def test_control_argv_and_env_are_the_shipped_official():
    plan = agent.plan_groundwork({})
    assert agent.cli_argv("do the task", plan) == [*_SHIPPED_CLI, "do the task"]
    base = {"PATH": "/usr/local/go/bin:/usr/bin", "HOME": "/tmp"}
    assert agent.cli_env(base, plan) == base  # unmodified copy


def test_cli_constant_matches_the_official_image():
    """The base CLI is byte-identical to the shipped anthropic-claude-code agent —
    the control arm forks nothing about the invocation [integration plan §4]."""
    official = _load("_official_cc_agent", _OFFICIAL_AGENT)
    assert agent.CLI == official.CLI == _SHIPPED_CLI


# --- treatment argv = control argv + only --mcp-config ---------------------
def test_treatment_argv_is_control_plus_only_mcp_config():
    control = agent.plan_groundwork({})
    treatment = agent.plan_groundwork(_TREATMENT)
    control_argv = agent.cli_argv("PROMPT", control)
    treatment_argv = agent.cli_argv("PROMPT", treatment)
    # the delta is EXACTLY the two --mcp-config tokens, inserted before the prompt
    assert treatment_argv == (
        control_argv[:-1] + ["--mcp-config", "/tmp/groundwork.mcp.json"] + control_argv[-1:]
    )
    # removing that two-token delta reproduces the control argv exactly
    assert treatment_argv == [*_SHIPPED_CLI, "--mcp-config", "/tmp/groundwork.mcp.json", "PROMPT"]
    stripped = list(treatment_argv)
    i = stripped.index("--mcp-config")
    del stripped[i : i + 2]
    assert stripped == control_argv


def test_treatment_env_prepends_bin_dir_to_path():
    plan = agent.plan_groundwork(_TREATMENT)
    env = agent.cli_env({"PATH": "/usr/bin"}, plan)
    assert env["PATH"] == "/tmp/.local/bin" + os.pathsep + "/usr/bin"


# --- image-directory self-consistency (no docker) --------------------------
def test_vendored_skill_present_with_frontmatter_and_provenance():
    skill = _IMG / "skill" / "groundwork-workflow" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n") and "name: groundwork-workflow" in text
    prov = (_IMG / "skill" / "PROVENANCE").read_text(encoding="utf-8")
    assert "golang-code-graph" in prov and re.search(r"[0-9a-f]{40}", prov)


def test_dockerfile_reuses_pinning_pattern_and_stages_off_path():
    df = (_IMG / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM verdi-base" in df
    assert "golang:1.25.11-bookworm" in df
    # the grader's fail-closed empty-ref check + prebuilt fallback, reused
    assert "GROUNDWORK_REF must be set" in df
    assert "GROUNDWORK_PREBUILT" in df
    # binaries staged at a non-PATH location (agent.py exposes them per-arm)
    assert "/opt/groundwork/bin" in df


def test_dockerfile_pins_a_skills_capable_cli_version():
    """The pinned CLI must support user-scope skills; agent skills first auto-loaded
    from ~/.claude/skills in @anthropic-ai/claude-code 2.1.157 (changelog)."""
    df = (_IMG / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"ARG CLAUDE_CODE_VERSION=(\d+)\.(\d+)\.(\d+)", df)
    assert m, "CLAUDE_CODE_VERSION must pin a concrete x.y.z default"
    assert tuple(int(g) for g in m.groups()) >= (2, 1, 157)


# --- docker-marked: a REAL build + arming smoke ----------------------------
from tests.fixtures.docker import DOCKER_AVAILABLE  # noqa: E402

_IMAGE_TAG = "verdi-bench/claude-code-groundwork-smoke:latest"
_FLOWMAP_BIN = os.environ.get("VERDI_FLOWMAP_BIN")
_GROUNDWORK_BIN = os.environ.get("VERDI_GROUNDWORK_BIN")
_BINARIES_SET = bool(_FLOWMAP_BIN and _GROUNDWORK_BIN)

_needs_daemon = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
_needs_binaries = pytest.mark.skipif(
    not _BINARIES_SET,
    reason="set VERDI_FLOWMAP_BIN and VERDI_GROUNDWORK_BIN (sibling-built) to bake "
    "the trial image via the prebuilt fallback (build is --pull for base images only)",
)


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def _build_image(tmp_path: Path) -> None:
    """Build verdi-base, then the trial image via the prebuilt fallback: assemble a
    self-contained context (a copy of the image dir) with the sibling binaries in
    bin/, so no `go install` (network) is needed at build time."""
    base = _run(["docker", "build", "-t", "verdi-base", str(_ROOT / "images" / "base")])
    assert base.returncode == 0, f"verdi-base build failed:\n{base.stderr[-2000:]}"
    ctx = tmp_path / "ctx"
    shutil.copytree(_IMG, ctx)
    shutil.copy(_FLOWMAP_BIN, ctx / "bin" / "flowmap")
    shutil.copy(_GROUNDWORK_BIN, ctx / "bin" / "groundwork")
    proc = _run([
        "docker", "build", "--build-arg", "GROUNDWORK_PREBUILT=1",
        "-t", _IMAGE_TAG, str(ctx),
    ])
    assert proc.returncode == 0, f"trial image build failed:\n{proc.stderr[-3000:]}"


@_needs_daemon
@_needs_binaries
def test_docker_binaries_baked_runnable_off_path_and_cli_installed(tmp_path):
    _build_image(tmp_path)
    # the staged binaries run from their absolute staging path
    for b in ("flowmap", "groundwork"):
        p = _run(["docker", "run", "--rm", "--entrypoint", f"/opt/groundwork/bin/{b}", _IMAGE_TAG, "version"])
        assert p.returncode == 0 and b in p.stdout, p.stderr
    # but they are NOT on the default PATH (control asymmetry) — only Go is
    onpath = _run(["docker", "run", "--rm", "--entrypoint", "bash", _IMAGE_TAG, "-lc",
                   "command -v flowmap || echo NONE"])
    assert onpath.stdout.strip() == "NONE", onpath.stdout
    # the vendored skill is staged and the CLI is installed
    skill = _run(["docker", "run", "--rm", "--entrypoint", "cat", _IMAGE_TAG,
                  "/opt/groundwork/skills/groundwork-workflow/SKILL.md"])
    assert skill.returncode == 0 and "groundwork-workflow" in skill.stdout
    cli = _run(["docker", "run", "--rm", "--entrypoint", "bash", _IMAGE_TAG, "-lc", "command -v claude"])
    assert cli.returncode == 0 and cli.stdout.strip()


_ARM_SMOKE = r"""
import os, sys, shutil, subprocess
sys.path.insert(0, '/')
import agent
plan = agent.plan_groundwork({'tools': ['groundwork']})
agent.apply_plan(plan)
home = os.environ.get('HOME') or '/tmp'
assert os.path.isfile(home + '/groundwork.mcp.json'), 'no MCP config in $HOME'
assert os.path.isfile(home + '/.claude/skills/groundwork-workflow/SKILL.md'), 'skill not in $HOME scope'
for b in ('flowmap', 'groundwork'):
    link = home + '/.local/bin/' + b
    assert os.path.islink(link) and os.access(link, os.X_OK), 'binary not exposed: ' + b
# the exposed binaries are findable on the treatment PATH (unambiguous: explicit
# path arg), and run via their symlink (absolute path avoids subprocess's parent-
# PATH resolution quirk)
env = agent.cli_env(os.environ, plan)
assert shutil.which('flowmap', path=env['PATH']) == home + '/.local/bin/flowmap'
out = subprocess.run([home + '/.local/bin/flowmap', 'version'], capture_output=True, text=True)
assert out.returncode == 0 and 'flowmap' in out.stdout, out.stderr
print('ARMED_OK')
"""

_CONTROL_SMOKE = r"""
import os, sys, shutil
sys.path.insert(0, '/')
import agent
plan = agent.plan_groundwork({})
agent.apply_plan(plan)
assert plan.enabled is False
home = os.environ.get('HOME') or '/tmp'
assert not os.path.exists(home + '/groundwork.mcp.json')
assert not os.path.exists(home + '/.claude/skills/groundwork-workflow')
assert not os.path.exists(home + '/.local/bin/flowmap')
assert shutil.which('flowmap') is None and shutil.which('groundwork') is None
assert agent.cli_argv('P', plan) == agent.CLI + ['P']  # shipped official argv
assert '--mcp-config' not in agent.cli_argv('P', plan)
print('CONTROL_OK')
"""


@_needs_daemon
@_needs_binaries
def test_docker_treatment_arms_from_home_scope_and_control_does_not(tmp_path):
    _build_image(tmp_path)
    armed = _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
                  _IMAGE_TAG, "-c", _ARM_SMOKE])
    assert armed.returncode == 0 and "ARMED_OK" in armed.stdout, armed.stderr
    control = _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
                    _IMAGE_TAG, "-c", _CONTROL_SMOKE])
    assert control.returncode == 0 and "CONTROL_OK" in control.stdout, control.stderr
