"""EVAL-4 AC-1 — the seam contract suite, run against BOTH engines.

The fake and Harbor engines must produce equivalent, well-formed records from
equivalent inputs. This parametrized suite is the contract; the fake is also the
fixture backbone for downstream stories.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from harness.adapters.base import ADVISORY, Outcome, Quotas, TrialRecord
from harness.hermetic.otlp_decode import resolve_spans
from harness.run.engines import ENGINES, get_engine, manages_real_infra
from harness.run.engines.base import ENGINE_FAILURE_REASONS
from harness.run.engines.fake import FakeEngine
from harness.run.engines.harbor import HarborEngine
from harness.run.seam import run_trial
from harness.run.types import OtlpConfig, ProxyConfig, RunConfig, Task, TrialRequest
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


@pytest.mark.parametrize("engine_name", list(ENGINES))
def test_ac1_engine_declares_infra_capability(engine_name):
    """[refactor 11 §G5c] Every registered engine declares manages_real_infra, so
    the managed proxy/collector wrapping (run/api.py) derives from the engine
    declaration via the registry helper — never an ``engine == "fake"`` string a new
    offline engine would have to know to imitate. The registry helper agrees with
    the engine's own class attribute."""
    flag = get_engine(engine_name).manages_real_infra
    assert isinstance(flag, bool)
    assert manages_real_infra(engine_name) is flag


def test_ac1_infra_capability_gates_fake_and_harbor():
    """The two engines declare explicitly: the fake is hermetic-by-fiat (no infra),
    Harbor containerizes — so the managed sidecars no-op the fake and wrap Harbor,
    byte-identically to the removed ``engine == "fake"`` literal [refactor 11 §G5c]."""
    assert manages_real_infra("fake") is False
    assert manages_real_infra("harbor") is True


# --- the OTLP span-capture ladder step, run through BOTH engines [refactor 09 §4] ---
# _read_span_log is inherited from EngineBase, so both engines drive one code path.
# The collector log is a host-side file (written by the real collector, or the fake's
# scripted parity); here the test pre-writes it, so the same log feeds both engines.
def _env_line(trial: str, seq: int, body: dict) -> str:
    return json.dumps(
        {"trial": trial, "seq": seq, "content_type": "application/json", "body_json": body}
    )


def _run_with_span_log(engine_name: str, root: Path, *, lines=None, write_log: bool = True):
    root.mkdir(parents=True, exist_ok=True)
    log = root / "otlp.jsonl"
    if write_log:
        log.write_text("".join(ln + "\n" for ln in (lines or [])), encoding="utf-8")
    otlp = OtlpConfig(endpoint="http://verdi-trace-collector:4318", log_path=str(log))
    config = replace(_configs()[engine_name], otlp=otlp)
    # the fake scripts no otlp_spans, so it does not also write — both engines read
    # exactly the pre-written host-side log through the shared _read_span_log.
    return run_trial(_task_for(engine_name), _arm(), root / "ws", config, trial_id="fixed-trial")


@pytest.mark.parametrize("engine_name", list(ENGINES))
def test_ac1_span_ladder_captures_and_verifies(engine_name, tmp_path):
    body = {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "op"}]}]}]}
    rec = _run_with_span_log(engine_name, tmp_path, lines=[_env_line("fixed-trial", 0, body)])
    assert rec.outcome == Outcome.completed
    assert rec.spans_sha is not None
    status, decoded = resolve_spans(rec.artifacts_path, rec.spans_sha)
    assert status == "verified"  # ledgered sha matches the on-disk artifact
    assert decoded.batches[0].resource_spans == body["resourceSpans"]


@pytest.mark.parametrize("engine_name", list(ENGINES))
def test_ac1_span_ladder_fail_closed_on_missing_log(engine_name, tmp_path):
    """A12 fail-closed, both engines: a configured collector whose log never
    appeared fails the trial infra_failed(span_log_missing), never 'zero spans'."""
    rec = _run_with_span_log(engine_name, tmp_path, write_log=False)
    assert rec.outcome == Outcome.infra_failed
    assert rec.flags.failure_reason == "span_log_missing"
    assert rec.flags.failure_reason in ENGINE_FAILURE_REASONS


@pytest.mark.parametrize("engine_name", list(ENGINES))
def test_ac1_span_ladder_zero_spans_is_empty_batches(engine_name, tmp_path):
    """Honest emptiness, both engines: a present-but-empty log yields an
    empty-batches artifact WITH a sha — distinct from absence (no collector)."""
    rec = _run_with_span_log(engine_name, tmp_path, lines=[])
    assert rec.outcome == Outcome.completed
    assert rec.spans_sha is not None
    status, decoded = resolve_spans(rec.artifacts_path, rec.spans_sha)
    assert status == "verified"
    assert decoded.batches == []


def test_ac1_both_engines_produce_identical_spans_sha(tmp_path):
    """One decode path: the same envelope log yields byte-identical span artifacts
    (and thus shas) regardless of which engine ran the trial."""
    body = {"resourceSpans": [{"k": "v"}]}
    line = _env_line("fixed-trial", 0, body)
    fake = _run_with_span_log("fake", tmp_path / "f", lines=[line])
    harbor = _run_with_span_log("harbor", tmp_path / "h", lines=[line])
    assert fake.spans_sha == harbor.spans_sha is not None


def test_ac1_no_otlp_configured_no_span_sha(tmp_path):
    """Absence: with no collector configured, no artifact and no sha — the
    honest 'not configured' state, distinct from empty-batches [refactor 09 §4]."""
    for engine_name in ENGINES:
        rec = run_trial(
            _task_for(engine_name), _arm(), tmp_path / engine_name, _configs()[engine_name]
        )
        assert rec.spans_sha is None
        assert resolve_spans(rec.artifacts_path, rec.spans_sha) == ("absent", None)


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
