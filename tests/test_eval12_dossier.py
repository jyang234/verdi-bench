"""EVAL-12 slice B — the comparison dossier: self-contained, fenced, honest.

AC-3: self-contained + byte-deterministic. AC-4: fence parity + watermark on
every layer. AC-5: verdict layer computed-only, uncertainty always present.
AC-6: side-by-side timelines, nulls render "not measured". AC-7: rides
``bench analyze`` with exactly one ``findings_rendered`` event.
"""

from __future__ import annotations

import re

import pytest
from jinja2 import Environment, meta

from harness.analyze.dossier import (
    NOT_MEASURED,
    VERDICT_ALLOWED_FIELDS,
    VERDICT_TEMPLATES,
    render_dossier,
    verdict_sentences,
)
from harness.analyze.report import (
    _WATERMARK,
    CalibrationIncompleteError,
    compute_findings,
    render_markdown,
)
from harness.analyze.timeline import trial_timeline
from harness.ledger.events import record_grade
from harness.ledger.query import find_events, verify
from harness.plan.interleave import Trial
from harness.run.engines.fake import FakeEngine
from harness.run.interleave import schedule
from harness.run.types import RunConfig, Task
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

_FAST = dict(coverage_n_sim=40, n_boot=500)

# One native log serving both platforms: the claude_code adapter reads
# `messages`, the codex adapter reads `events` — so a single task fixture
# yields real trajectories on both arms.
_NATIVE_BOTH = {
    "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0},
    "total_cost_usd": 0.01,
    "duration_ms": 1000,
    "tool_use_count": 1,
    "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "elapsed_seconds": 1.0,
    "tool_calls": 1,
    "messages": [
        {"content": [{"type": "text", "text": "hi"}]},
        {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]},
    ],
    "events": [
        {"type": "message", "elapsed_s": 0.2},
        {"type": "exec", "elapsed_s": 0.8, "parsed_cmd": "test", "exit_code": 0},
    ],
}


def _seeded_findings(tmp_path, *, control_pass=lambda i: i < 3, treatment_pass=lambda i: i < 1):
    """A locked, populated experiment via the seed-builders (no run stage)."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    for i in range(5):
        for rep in range(2):
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"c-{i}-{rep}", task_id=f"task{i}", arm="control",
                repetition=rep, passed=control_pass(i),
            )
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"t-{i}-{rep}", task_id=f"task{i}", arm="treatment",
                repetition=rep, passed=treatment_pass(i),
            )
    findings = compute_findings(ledger, spec, spec.seed, **_FAST)
    return spec, ledger, findings


def _run_experiment(tmp_path):
    """A locked experiment whose trials actually ran (real trajectories)."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    arms = {a.name: a for a in spec.arms}
    tasks = {
        f"task{i}": Task(id=f"task{i}", prompt="p", fake_behavior={"native_log": _NATIVE_BOTH})
        for i in range(5)
    }
    order = []
    for i in range(5):
        order.append(Trial(task_id=f"task{i}", arm="control", repetition=0))
        order.append(Trial(task_id=f"task{i}", arm="treatment", repetition=0))
    res = schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=ledger, ctx=ctx, config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    for rec in res.records:
        record_grade(
            ledger, ctx, trial_id=rec.trial_id, task_sha=f"sha-{rec.task_id}",
            assertions=[{"id": "h1", "source": "holdout_test",
                         "result": "pass" if rec.arm == "control" else "fail"}],
            binary_score=rec.arm == "control",
        )
    findings = compute_findings(ledger, spec, spec.seed, **_FAST)
    return spec, ledger, findings


def _layer_chunks(dossier: str) -> dict[str, str]:
    """Split the artifact into its three layer sections by id."""
    chunks = {}
    parts = re.split(r'<section class="layer" id="layer-([a-z]+)">', dossier)
    for i in range(1, len(parts), 2):
        chunks[parts[i]] = parts[i + 1]
    return chunks


# --- AC-3: self-contained + byte-deterministic --------------------------------
def test_ac3_self_contained_deterministic(tmp_path):
    _, ledger, findings = _seeded_findings(tmp_path)
    one = render_dossier(findings, ledger, "exploratory")
    two = render_dossier(findings, ledger, "exploratory")
    assert one == two  # byte-identical for a fixed (ledger, seed)

    # no external URI schemes, no fetched assets, no scripts — archivable
    # air-gapped, nothing leaks to a scrapeable surface [AC-3]
    for needle in ("http://", "https://", "src=", "href=", "url(", "@import", "<script", "<link"):
        assert needle not in one, f"external/active reference {needle!r} in dossier"
    assert one.startswith("<!doctype html>")
    assert set(_layer_chunks(one)) == {"verdict", "analyst", "auditor"}


def test_arm_markup_lands_inert(tmp_path):
    """AN-5 discipline: an arm name carrying markup renders escaped, everywhere."""
    ctx = fixed_ctx()
    evil = "<script>alert(1)</script>"
    spec, _, ledger = locked_experiment(
        tmp_path, ctx=ctx,
        arms=[
            {"name": evil, "platform": "claude_code",
             "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
            {"name": "treatment", "platform": "codex",
             "model": "openai/gpt-4o-2024-08-06", "payload": {}},
        ],
    )
    for i in range(2):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}", task_id=f"task{i}",
                             arm=evil, passed=True)
        seed_trial_and_grade(ledger, ctx, trial_id=f"t-{i}", task_id=f"task{i}",
                             arm="treatment", passed=False)
    findings = compute_findings(ledger, spec, spec.seed, **_FAST)
    dossier = render_dossier(findings, ledger, "exploratory")
    assert "<script" not in dossier
    assert "&lt;script&gt;" in dossier


def test_verdict_escapes_once(tmp_path):
    """The decision rule always contains a comparator; it must render as a
    single-escaped entity, never double-escaped into visible '&gt;' text."""
    _, ledger, findings = _seeded_findings(tmp_path)
    assert ">" in findings.decision_rule  # the fixture rule is 'delta_... > 0'
    dossier = render_dossier(findings, ledger, "exploratory")
    verdict = _layer_chunks(dossier)["verdict"]
    assert "&gt;" in verdict  # escaped exactly once
    assert "&amp;gt;" not in dossier  # never twice


# --- AC-4: fence parity + watermark on every layer -----------------------------
def test_ac4_fence_parity(tmp_path):
    """A fence-refusing ledger refuses the official dossier with the same
    AnalyzeError (⇒ the same cant_analyze reason) as the markdown render."""
    _, ledger, findings = _seeded_findings(tmp_path)
    with pytest.raises(CalibrationIncompleteError):
        render_markdown(findings, ledger, "official", corpus_manifest=None)
    with pytest.raises(CalibrationIncompleteError):
        render_dossier(findings, ledger, "official", corpus_manifest=None)


def test_ac4_watermark_every_layer(tmp_path):
    _, ledger, findings = _seeded_findings(tmp_path)
    dossier = render_dossier(findings, ledger, "exploratory")
    chunks = _layer_chunks(dossier)
    assert set(chunks) == {"verdict", "analyst", "auditor"}
    for name, chunk in chunks.items():
        assert _WATERMARK in chunk, f"layer {name} missing the EXPLORATORY watermark"
        # ADVISORY banner carries into every layer too (fixture trials are
        # local ⇒ ADVISORY tier) [AC-4]
        assert "ADVISORY" in chunk, f"layer {name} missing the ADVISORY banner"


# --- AC-5: verdict layer is computed-only, uncertainty always present ----------
def test_ac5_verdict_layer_computed_only(tmp_path):
    # Template inventory: every verdict-layer sentence template interpolates
    # only fields from the closed [computed] findings-derived set [D003].
    env = Environment(autoescape=True)
    for name, template in VERDICT_TEMPLATES.items():
        variables = meta.find_undeclared_variables(env.parse(template))
        assert variables <= VERDICT_ALLOWED_FIELDS, (
            f"verdict template {name!r} interpolates non-findings fields "
            f"{sorted(variables - VERDICT_ALLOWED_FIELDS)}"
        )

    # A null result renders the pre-registered null phrasing, never "no
    # difference" — identical arms produce all-zero deltas.
    _, ledger, findings = _seeded_findings(
        tmp_path, control_pass=lambda i: True, treatment_pass=lambda i: True
    )
    dossier = render_dossier(findings, ledger, "exploratory")
    verdict = _layer_chunks(dossier)["verdict"]
    assert "No effect ≥ MDE detected" in verdict
    assert "no difference" not in dossier.lower()

    # Structural guarantee: every verdict sentence came from the registry.
    sentences = verdict_sentences(findings, findings.comparisons[0])
    for s in sentences:
        assert any(
            s.startswith(t.split("{{")[0]) for t in VERDICT_TEMPLATES.values()
        ), f"verdict sentence not template-derived: {s!r}"


def test_ac5_uncertainty_always_present(tmp_path):
    _, ledger, findings = _seeded_findings(tmp_path)
    verdict = _layer_chunks(render_dossier(findings, ledger, "exploratory"))["verdict"]
    assert "% CI" in verdict
    assert "MDE=" in verdict
    assert "N=" in verdict

    # underpowered fixture ⇒ the MDE caveat sentence renders
    under = findings.model_copy(
        update={"mde": findings.mde.model_copy(update={"acknowledged_underpowered": True})}
    )
    sentences = verdict_sentences(under, under.comparisons[0])
    assert any("underpowered" in s for s in sentences)

    # no paired data ⇒ uncertainty still present (N and MDE), no CI claimed
    empty = findings.comparisons[0].model_copy(
        update={"stats": {}, "n_tasks": 0, "exclusion_reason": "no paired task data"}
    )
    sentences = verdict_sentences(findings, empty)
    joined = " ".join(sentences)
    assert "no confidence interval" in joined and "MDE=" in joined and "N=0" in joined


# --- AC-6: side-by-side timelines; nulls never zero ----------------------------
def test_ac6_side_by_side_timelines(tmp_path):
    _, ledger, findings = _run_experiment(tmp_path)
    timelines = trial_timeline(ledger)
    # both arms' trials for a task land in one task view, steps verified
    assert set(timelines["task0"]) == {"control", "treatment"}
    for arm in ("control", "treatment"):
        assert all(t["trajectory_status"] == "verified" for t in timelines["task0"][arm])

    dossier = render_dossier(findings, ledger, "exploratory")
    analyst = _layer_chunks(dossier)["analyst"]
    task0 = analyst.split("<h4>task task0</h4>")[1].split("<h4>task task1</h4>")[0]
    assert "control" in task0 and "treatment" in task0  # one view, both arms

    # the auditor layer's chain status matches verify_chain's verdict
    auditor = _layer_chunks(dossier)["auditor"]
    assert f"chain_ok={verify(ledger).ok}" in auditor


def test_unreadable_artifact_is_coverage_data_not_a_crash(tmp_path):
    """A present-but-unreadable trajectory artifact is a 'corrupt' coverage gap;
    it must never escape the render as a raw OSError (the AN-3 envelope only
    catches AnalyzeError, so an escape means zero ledger events)."""
    from pathlib import Path

    _, ledger, findings = _run_experiment(tmp_path)
    ev = find_events(ledger, "trial")[0]
    artifact = Path(ev["trial_record"]["artifacts_path"]) / "trajectory.json"
    artifact.unlink()
    artifact.mkdir()  # read_bytes now raises IsADirectoryError

    timelines = trial_timeline(ledger)
    statuses = [
        t["trajectory_status"]
        for arms in timelines.values()
        for rows in arms.values()
        for t in rows
    ]
    assert "corrupt" in statuses
    dossier = render_dossier(findings, ledger, "exploratory")  # must not raise
    assert "corrupt: 1 trial(s)" in _layer_chunks(dossier)["auditor"]


def test_ac6_null_never_zero(tmp_path):
    """A trial whose telemetry is unmeasured renders 'not measured' — never 0."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    # null telemetry across the board (empty Telemetry ⇒ every field None)
    seed_trial_and_grade(ledger, ctx, trial_id="c-0", task_id="task0", arm="control",
                         passed=True, telemetry={})
    seed_trial_and_grade(ledger, ctx, trial_id="t-0", task_id="task0", arm="treatment",
                         passed=False, telemetry={})
    findings = compute_findings(ledger, spec, spec.seed, **_FAST)
    dossier = render_dossier(findings, ledger, "exploratory")
    analyst = _layer_chunks(dossier)["analyst"]
    assert f"wall time: {NOT_MEASURED}" in analyst
    # the null fields are named, and no zero is fabricated for any of them
    assert f"{NOT_MEASURED}: tokens_in, tokens_out, tokens_cache, cost" in analyst
    assert "wall time: 0.0" not in dossier


# --- AC-7: rides bench analyze, one findings_rendered event --------------------
def test_ac7_rides_analyze_one_event(tmp_path):
    from typer.testing import CliRunner

    from harness.cli import app

    ctx = fixed_ctx()
    _, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    for i in range(5):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}", task_id=f"task{i}",
                             arm="control", passed=i < 3)
        seed_trial_and_grade(ledger, ctx, trial_id=f"t-{i}", task_id=f"task{i}",
                             arm="treatment", passed=False)

    result = CliRunner().invoke(
        app, ["analyze", str(tmp_path), "--exploratory", "--actor", "tester"]
    )
    assert result.exit_code == 0, result.output

    md = tmp_path / "findings.exploratory.md"
    dossier = tmp_path / "findings.exploratory.dossier.html"
    assert md.exists() and dossier.exists()  # dossier beside the markdown
    assert dossier.read_text(encoding="utf-8").startswith("<!doctype html>")
    # exactly one findings_rendered event for the whole invocation [D004]
    assert len(find_events(ledger, "findings_rendered")) == 1

    # the README documents the artifact (mechanical doc coverage, XC-7 spirit)
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "dossier.html" in readme
