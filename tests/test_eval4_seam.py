"""EVAL-4 AC-1 — the seam contract suite, run against BOTH engines.

The fake and Harbor engines must produce equivalent, well-formed records from
equivalent inputs. This parametrized suite is the contract; the fake is also the
fixture backbone for downstream stories.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.adapters.base import ADVISORY, Outcome, Quotas, TrialRecord
from harness.run.engines import ENGINES
from harness.run.engines.base import ENGINE_FAILURE_REASONS
from harness.run.engines.fake import FakeEngine
from harness.run.engines.harbor import HarborEngine
from harness.run.seam import run_trial
from harness.run.types import ProxyConfig, RunConfig, Task, TrialRequest
from harness.schema.experiment import Arm
from tests.fixtures.run_fakes import FakeDockerRunner

NATIVE_LOG = {
    "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10},
    "total_cost_usd": 0.02,
    "duration_ms": 4200,
    "tool_use_count": 3,
}


def _arm():
    return Arm(name="control", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def _configs():
    fake = RunConfig(engine=FakeEngine())
    harbor = RunConfig(engine=HarborEngine(runner=FakeDockerRunner(native_log=NATIVE_LOG)))
    return {"fake": fake, "harbor": harbor}


def _task_for(engine_name: str) -> Task:
    if engine_name == "fake":
        return Task(id="t1", prompt="do the thing", fake_behavior={"native_log": NATIVE_LOG})
    return Task(id="t1", prompt="do the thing")


@pytest.mark.parametrize("engine_name", list(ENGINES))
def test_ac1_seam_contract(engine_name, tmp_path):
    config = _configs()[engine_name]
    rec = run_trial(_task_for(engine_name), _arm(), tmp_path / "ws", config)
    assert isinstance(rec, TrialRecord)
    assert rec.task_id == "t1"
    assert rec.arm == "control"
    assert rec.outcome == Outcome.completed
    # telemetry normalized identically from the same native log
    assert rec.telemetry.tokens_in == 100
    assert rec.telemetry.tokens_out == 50
    assert rec.telemetry.cost == 0.02
    assert rec.telemetry.tool_calls == 3
    assert rec.telemetry_nulls == []  # all fields measured
    assert rec.provenance.tier == ADVISORY
    # template obligations [refactor 04 §2]: agent_log lives under <ws>/artifacts,
    # and the digest is an immutable sha256 content address.
    assert Path(rec.artifacts_path).name == "artifacts"
    assert (Path(rec.artifacts_path) / "agent_log.json").exists()
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", rec.provenance.image_digest)


@pytest.mark.parametrize("engine_name", list(ENGINES))
def test_ac1_record_shape_stable(engine_name, tmp_path):
    config = _configs()[engine_name]
    rec = run_trial(_task_for(engine_name), _arm(), tmp_path / "ws", config)
    # round-trips through the pydantic contract
    dumped = rec.model_dump(mode="json")
    assert TrialRecord.model_validate(dumped) == rec


def _request(engine_name: str, workspace: Path) -> TrialRequest:
    task = _task_for(engine_name)
    return TrialRequest(
        trial_id="trial-x", task_id=task.id, prompt=task.prompt, image=task.image,
        arm=_arm(), repetition=0, workspace=workspace, quotas=Quotas(), timeout_s=60,
        ts="2026-01-01T00:00:00+00:00", fake_behavior=task.fake_behavior,
    )


@pytest.mark.parametrize("engine_name", list(ENGINES))
def test_ac1_native_log_matches_on_disk_pre_redaction(engine_name, tmp_path):
    """Dual-source invariant [refactor 04 §2, seam.py:171,208]: an engine's
    in-memory native_log (the telemetry source) equals the on-disk pre-redaction
    agent_log.json (the trajectory source before the seam scrubs it). Asserted at the
    engine, before the seam redacts, so the two sources are provably the same bytes —
    a scripted engine that returned a native_log diverging from what it wrote would be
    caught here."""
    engine = _configs()[engine_name].engine
    result = engine.run(_request(engine_name, tmp_path / "ws"))
    on_disk = json.loads((Path(result.artifacts_dir) / "agent_log.json").read_text())
    assert result.native_log == on_disk == NATIVE_LOG


def test_ac1_failure_reason_in_closed_vocabulary(tmp_path):
    """Every REAL infra failure_reason an engine stamps is a member of the closed
    ENGINE_FAILURE_REASONS vocabulary [refactor 04 §2], routed through the shared
    ladder: Harbor a docker daemon_error, the fake an ORGANIC proxy_log_missing (a
    configured proxy whose log never appears — A10 parity, not a scripted string)."""
    harbor = HarborEngine(runner=FakeDockerRunner(native_log={}, daemon_error=True))
    hrec = run_trial(Task(id="t", prompt="p"), _arm(), tmp_path / "h", RunConfig(engine=harbor))
    assert hrec.outcome == Outcome.infra_failed
    assert hrec.flags.failure_reason == "daemon_error"
    assert hrec.flags.failure_reason in ENGINE_FAILURE_REASONS

    proxy = ProxyConfig(proxy_url="http://p:3128", log_path=str(tmp_path / "missing.jsonl"))
    frec = run_trial(
        Task(id="t", prompt="p", fake_behavior={"native_log": {}}), _arm(),
        tmp_path / "f", RunConfig(engine=FakeEngine(), proxy=proxy),
    )
    assert frec.outcome == Outcome.infra_failed
    assert frec.flags.failure_reason == "proxy_log_missing"
    assert frec.flags.failure_reason in ENGINE_FAILURE_REASONS


# Only the engine module and the engine factory may name Harbor.
_HARBOR_ALLOWED = {"harness/run/engines/harbor.py", "harness/run/engines/__init__.py"}


def _harbor_offenders(repo_root, source_text=None, source_rel=None):
    """Return (rel_path, imported_name) for any module outside the seam that
    NAMES harbor/docker. Inspects both the from-module and the imported member
    names, so ``from .engines import harbor`` (member = the harbor MODULE) is
    caught, not just ``import ...harbor`` [7H-1].

    ``source_text``/``source_rel`` inject one module's source in-memory so a
    planted violation can be checked without writing the tree."""
    import ast

    root = repo_root / "harness"
    offenders = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(repo_root).as_posix()
        if rel in _HARBOR_ALLOWED:
            continue
        text = source_text if source_rel == rel else py.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                # module AND member names: `from .engines import harbor` has
                # node.module=".engines" and would evade a module-only scan. The
                # engines/__init__ factory seam is exempt via _HARBOR_ALLOWED.
                names = [node.module or ""] + [a.name for a in node.names]
            for name in names:
                last = name.rsplit(".", 1)[-1]
                if last == "harbor" or name == "docker":
                    offenders.append((rel, name))
    return offenders


def test_ac1_engine_isolated():
    """No module outside the run-engine seam imports Harbor [import-linter].

    Assert here too: only harness.run.engines.harbor references docker/Harbor.
    """
    import pathlib

    # Anchor on __file__, not the cwd: a relative Path("harness") globs nothing
    # from any other working directory and the scan would pass vacuously [XC-5].
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    assert list((repo_root / "harness").rglob("*.py")), "seam scan found no modules"
    offenders = _harbor_offenders(repo_root)
    assert not offenders, f"Harbor/docker imported outside the seam: {offenders}"


def test_7h1_ast_scan_catches_package_init_harbor_import():
    """7H-1 reproduce-first: `from .engines import harbor` planted in a package
    __init__ (harness/run/__init__.py) — evaded by the old module-only scan —
    is now caught by the member-name inspection."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rel = "harness/run/__init__.py"
    original = (repo_root / rel).read_text(encoding="utf-8")
    planted = original + "\nfrom .engines import harbor  # planted violation\n"
    offenders = _harbor_offenders(repo_root, source_text=planted, source_rel=rel)
    assert (rel, "harbor") in offenders
