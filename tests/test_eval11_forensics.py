"""EVAL-11 integration — scan verb, findings, renders, quarantine [AC-5, AC-6].

Forensic flags render beside their comparison in the markdown renders and ride
every dossier layer, non-suppressing; forensic metric ids can never validate as
a primary metric; partial coverage is disclosed with per-trial reasons; the
operator quarantine excludes with disclosure [D007].
"""

from __future__ import annotations

import json

import pytest
import yaml

from harness.analyze.dossier import render_dossier
from harness.analyze.report import compute_findings, render_markdown
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.forensics.cli import run_forensics
from harness.forensics.metrics import FORENSICS_VOCABULARY_VERSION, METRIC_IDS
from harness.judge.providers.base import ProviderError
from harness.judge.providers.fake import DeterministicFakeProvider, FakeProvider
from harness.ledger.events import (
    record_calibration_run,
    record_forensic_quarantine,
    record_forensic_spotcheck,
    record_forensics_report,
    record_grade,
)
from harness.ledger.query import find_events
from harness.plan.interleave import Trial
from harness.run.engines.fake import FakeEngine
from harness.run.interleave import schedule
from harness.run.types import RunConfig, Task
from harness.schema.errors import SpecError
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import (
    fixed_ctx,
    locked_experiment,
    seed_trial_and_grade,
    valid_experiment_dict,
)

_FAST = dict(coverage_n_sim=40, n_boot=500)


# --- fixture helpers -----------------------------------------------------------
def _full_corpus():
    m = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha="a" * 64, status="admitted") for i in range(5)],
    )
    m.calibration.status = "full-run-validated"
    return m


def _seed_official_gates(ledger, ctx, spec):
    """Calibration + passing selfcheck so the official fence opens (the
    test_eval6 fixture pattern) — forensics itself must never be a gate."""
    from harness.analyze.selfcheck import run_selfcheck
    from harness.ledger.events import record_selfcheck

    record_calibration_run(
        ledger, ctx, corpus_id="public-mini", semver="1.0.0", kind="full",
        run={"p": 0.5, "rho": 0.3, "n_tasks": 5}, status="full-run-validated",
    )
    res = run_selfcheck(ledger, spec, n_sim=40, n_boot=500)
    res["passed"] = True
    record_selfcheck(ledger, ctx, **res)


def _populate(ledger, ctx):
    for i in range(5):
        for rep in range(2):
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"c-{i}-{rep}", task_id=f"task{i}",
                arm="control", repetition=rep, passed=i < 3,
            )
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"t-{i}-{rep}", task_id=f"task{i}",
                arm="treatment", repetition=rep, passed=i < 1,
            )


_TAMPER_FLAG = {
    "detector": "holdout_tamper",
    "paths": ["/ws/holdouts/test_hidden.py"],
    "trial_id": "c-0-0",
    "task_id": "task0",
    "arm": "control",
}


def _seed_forensics_report(ledger, ctx, *, flags=(), gaps=(), covered=10, trials=10):
    record_forensics_report(
        ledger, ctx,
        forensics_report={
            "vocabulary_version": FORENSICS_VOCABULARY_VERSION,
            "metrics": {},
            "flags": list(flags),
            "coverage": {"trials": trials, "covered": covered, "gaps": list(gaps)},
        },
    )


def _official_findings(tmp_path, *, flags=(), gaps=(), covered=10):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    _populate(ledger, ctx)
    _seed_forensics_report(ledger, ctx, flags=flags, gaps=gaps, covered=covered)
    _seed_official_gates(ledger, ctx, spec)
    return spec, ledger, compute_findings(ledger, spec, spec.seed, **_FAST)


# --- AC-5: primary ineligibility -------------------------------------------------
def test_ac5_primary_ineligible():
    """Registering any forensic metric as primary_metric fails EVAL-3 schema
    validation — the closed PrimaryMetric vocabulary is unchanged [AC-5 VC]."""
    for metric_id in METRIC_IDS:
        with pytest.raises(SpecError):
            ExperimentSpec.from_dict(valid_experiment_dict(primary_metric=metric_id))


# --- AC-5: flags render beside the comparison, non-suppressing -------------------
def test_ac5_flags_render_beside_comparison(tmp_path):
    spec, ledger, findings = _official_findings(tmp_path, flags=[_TAMPER_FLAG])

    official = render_markdown(findings, ledger, "official", corpus_manifest=_full_corpus())
    exploratory = render_markdown(findings, ledger, "exploratory")
    for render in (official, exploratory):
        comparison_at = render.index("Comparison: control vs treatment")
        flag_at = render.index("forensic flag [holdout_tamper]: trial c-0-0")
        next_heading = render.index("Confounds", comparison_at)
        # the flag sits inside the comparison block, before the next section
        assert comparison_at < flag_at < next_heading
        assert "## Forensic flags (disclosed, non-suppressing)" in render or (
            "Forensic flags (disclosed, non-suppressing)" in render
        )
        assert "evidence, not a verdict" in render


def test_flags_ride_every_dossier_layer(tmp_path):
    import re

    spec, ledger, findings = _official_findings(tmp_path, flags=[_TAMPER_FLAG])
    dossier = render_dossier(findings, ledger, "exploratory")
    layers = re.split(r'<section class="layer" id="layer-([a-z]+)">', dossier)
    chunks = {layers[i]: layers[i + 1] for i in range(1, len(layers), 2)}
    assert set(chunks) == {"verdict", "analyst", "auditor"}
    for name, chunk in chunks.items():
        assert "Forensic flags (disclosed, non-suppressing)" in chunk, name
        assert "holdout_tamper" in chunk, name


def test_flags_suppress_nothing(tmp_path):
    """Non-suppressing [D003/D004]: identical data with and without a planted
    flag yields identical comparison statistics, and the flagged official
    render is not refused."""
    ctx_a, ctx_b = fixed_ctx(), fixed_ctx()
    spec_a, _, ledger_a = locked_experiment(tmp_path / "a", ctx=ctx_a)
    spec_b, _, ledger_b = locked_experiment(tmp_path / "b", ctx=ctx_b)
    _populate(ledger_a, ctx_a)
    _populate(ledger_b, ctx_b)
    _seed_forensics_report(ledger_b, ctx_b, flags=[_TAMPER_FLAG])
    f_a = compute_findings(ledger_a, spec_a, spec_a.seed, **_FAST)
    f_b = compute_findings(ledger_b, spec_b, spec_b.seed, **_FAST)
    assert [c.model_dump() for c in f_a.comparisons] == [
        c.model_dump() for c in f_b.comparisons
    ]


# --- AC-6: coverage honesty ------------------------------------------------------
def test_ac6_partial_coverage_disclosed(tmp_path):
    """A trajectory-less trial renders its gap with trial id + reason [AC-6 VC]."""
    spec, ledger, findings = _official_findings(
        tmp_path,
        gaps=[{"trial_id": "t-4-1", "reason": "absent"}],
        covered=9,
    )
    md = render_markdown(findings, ledger, "exploratory")
    assert "coverage gap: trial t-4-1 — absent" in md
    assert "9/10 trial(s) profiled" in md


def test_full_coverage_renders_no_gap_line(tmp_path):
    spec, ledger, findings = _official_findings(tmp_path, gaps=(), covered=10)
    md = render_markdown(findings, ledger, "exploratory")
    assert "coverage gap" not in md
    assert "10/10 trial(s) profiled" in md


# --- the scan verb end-to-end -----------------------------------------------------
_NATIVE_WITH_TRAJECTORY = {
    "messages": [
        {"content": [{"type": "text", "text": "plan"}]},
        {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "src/a.py"}}]},
        {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]},
    ]
}


def _run_scan_experiment(tmp_path, *, tamper=False, absent_trajectory=False):
    """A locked experiment whose control-arm trials really ran (fake engine),
    with a tasks.yaml + holdout files so scan assembles real evidence."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    holdouts = tmp_path / "holdouts"
    holdouts.mkdir()
    (holdouts / "test_hidden.py").write_text(
        'def test_secret():\n    assert answer() == "expected-secret-value"\n',
        encoding="utf-8",
    )
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump(
            {"tasks": [{"id": "task0", "prompt": "p", "holdouts_dir": str(holdouts)}]}
        ),
        encoding="utf-8",
    )
    native = dict(_NATIVE_WITH_TRAJECTORY)
    if tamper:
        native = {
            "messages": _NATIVE_WITH_TRAJECTORY["messages"]
            + [{"content": [{"type": "tool_use", "name": "Edit",
                             "input": {"file_path": str(holdouts / "test_hidden.py")}}]}]
        }
    behavior = {} if absent_trajectory else {"native_log": native}
    tasks = {"task0": Task(id="task0", prompt="p", fake_behavior=behavior)}
    res = schedule(
        [Trial(task_id="task0", arm="control", repetition=0)],
        tasks=tasks, arms={"control": spec.arms[0]}, workspace_root=tmp_path / "ws",
        ledger_path=ledger, ctx=ctx, config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    for rec in res.records:
        record_grade(
            ledger, ctx, trial_id=rec.trial_id, task_sha="sha-task0",
            assertions=[{"id": "h1", "source": "holdout_test", "result": "pass"}],
            binary_score=True,
        )
    return spec, ledger, ctx


def test_scan_emits_one_event_with_metrics_and_version(tmp_path):
    spec, ledger, ctx = _run_scan_experiment(tmp_path)
    before = len(find_events(ledger, "forensics_report"))
    report = run_forensics(tmp_path, ctx=ctx, review=False)
    assert len(find_events(ledger, "forensics_report")) == before + 1
    assert report["vocabulary_version"] == FORENSICS_VOCABULARY_VERSION
    (trial_metrics,) = report["metrics"].values()
    assert tuple(trial_metrics) == METRIC_IDS
    assert report["coverage"] == {"trials": 1, "covered": 1, "gaps": []}
    assert report["flags"] == []  # honest trial: clean scan


def test_scan_flags_planted_tamper_and_absent_trajectory(tmp_path):
    spec, ledger, ctx = _run_scan_experiment(tmp_path / "tampered", tamper=True)
    report = run_forensics(tmp_path / "tampered", ctx=ctx, review=False)
    assert [f["detector"] for f in report["flags"]] == ["holdout_tamper"]

    spec2, ledger2, ctx2 = _run_scan_experiment(tmp_path / "bare", absent_trajectory=True)
    report2 = run_forensics(tmp_path / "bare", ctx=ctx2, review=False)
    assert report2["metrics"] == {}
    (gap,) = report2["coverage"]["gaps"]
    assert gap["reason"] == "absent"


def test_scan_reviews_ride_the_report_and_fail_closed(tmp_path):
    spec, ledger, ctx = _run_scan_experiment(tmp_path / "ok")
    report = run_forensics(
        tmp_path / "ok", ctx=ctx, review=True, provider=DeterministicFakeProvider()
    )
    (review,) = report["reviews"].values()
    assert review["suspicions"] is not None
    assert review["narrative"].startswith("[judgment]")

    spec2, ledger2, ctx2 = _run_scan_experiment(tmp_path / "down")
    report2 = run_forensics(
        tmp_path / "down", ctx=ctx2, review=True, provider=FakeProvider([ProviderError("x")])
    )
    (review2,) = report2["reviews"].values()
    assert review2["cant_review_reason"] == "provider_error"


# --- spot-check kappa reaches the render ------------------------------------------
def test_spotcheck_kappa_table_in_exploratory_render(tmp_path):
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    _populate(ledger, ctx)
    suspicions_yes = {d: True for d in
                      ("holdout_tamper", "hardcoded_expected_output",
                       "test_skip_insertion", "suspicious_single_step")}
    suspicions_no = {d: False for d in suspicions_yes}
    record_forensics_report(
        ledger, ctx,
        forensics_report={
            "vocabulary_version": 1, "metrics": {}, "flags": [],
            "coverage": {"trials": 10, "covered": 10, "gaps": []},
            "reviews": {
                "c-0-0": {"trial_id": "c-0-0", "suspicions": suspicions_yes,
                          "narrative": "[judgment] gamed", "cant_review_reason": None},
                "c-1-0": {"trial_id": "c-1-0", "suspicions": suspicions_no,
                          "narrative": "[judgment] honest", "cant_review_reason": None},
            },
        },
    )
    record_forensic_spotcheck(ledger, ctx, trial_id="c-0-0",
                              labels={k: True for k in suspicions_yes}, stratum="mandatory")
    record_forensic_spotcheck(ledger, ctx, trial_id="c-1-0",
                              labels={k: False for k in suspicions_yes}, stratum="floor")
    findings = compute_findings(ledger, spec, spec.seed, **_FAST)
    kappa = findings.forensics["spotcheck_kappa"]["kappa_by_detector"]
    assert kappa["holdout_tamper"]["kappa"] == 1.0  # perfect agreement fixture
    md = render_markdown(findings, ledger, "exploratory")
    assert "LLM↔human agreement (unweighted IPW kappa, per detector)" in md
    assert "holdout_tamper: kappa=1.000" in md


# --- D007: quarantine excludes + discloses ----------------------------------------
def test_quarantine_excludes_and_discloses(tmp_path):
    ctx_a, ctx_b = fixed_ctx(), fixed_ctx()
    spec_a, _, ledger_a = locked_experiment(tmp_path / "a", ctx=ctx_a)
    spec_b, _, ledger_b = locked_experiment(tmp_path / "b", ctx=ctx_b)
    _populate(ledger_a, ctx_a)
    _populate(ledger_b, ctx_b)
    # quarantining BOTH of task4's control trials unpairs the task: its data
    # leaves the comparison entirely, with disclosure [D007]
    for trial_id in ("c-4-0", "c-4-1"):
        record_forensic_quarantine(
            ledger_b, ctx_b, trial_id=trial_id, reason="confirmed holdout tamper"
        )
    f_a = compute_findings(ledger_a, spec_a, spec_a.seed, **_FAST)
    f_b = compute_findings(ledger_b, spec_b, spec_b.seed, **_FAST)
    assert f_a.comparisons[0].n_tasks == 5
    assert f_b.comparisons[0].n_tasks == 4
    assert (
        f_a.comparisons[0].effect["mean_paired_delta"]
        != f_b.comparisons[0].effect["mean_paired_delta"]
    )

    md = render_markdown(f_b, ledger_b, "exploratory")
    assert "QUARANTINED by operator tester: trial c-4-0 — confirmed holdout tamper" in md
    assert "excluded from comparisons" in md


def test_quarantine_never_automatic(tmp_path):
    """A flag alone changes nothing — only the ledgered operator event does.
    (The flags-suppress-nothing test proves the flag half; here the quarantine
    event alone, with no flag, is honored — dispositions are human acts.)"""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    _populate(ledger, ctx)
    record_forensic_quarantine(ledger, ctx, trial_id="c-4-0", reason="operator call")
    findings = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert findings.forensics["quarantined"] == [
        {"trial_id": "c-4-0", "reason": "operator call", "actor": "tester"}
    ]
