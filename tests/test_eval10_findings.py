"""Findings integration: contamination summary, caveats, and the fence [EVAL-10 AC-5]."""

from __future__ import annotations

import pytest

from harness.analyze.report import (
    AsymmetricContaminationError,
    CantAnalyzeReason,
    cant_analyze_reason,
    compute_findings,
    render_markdown,
)
from harness.contamination.canary import derive_canary
from harness.contamination.probe import ProbeTask, run_memory_probe
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.judge.providers.fake import FakeProvider
from tests.fixtures.builders import fixed_ctx, locked_experiment
from tests.test_eval6_analyze import (
    _FAST,
    _populate,
    _seed_full_calibration,
    _seed_matching_selfcheck,
)

_SHA = "a" * 64  # matches the eval6 _full_corpus task shas

# both arms date-clean on every task: created strictly after both cutoffs, so
# any flag below is a pure detection, exactly the spec's asymmetric-vs-clean case
_ARMS_WITH_CUTOFFS = [
    {"name": "control", "platform": "claude_code",
     "model": "anthropic/claude-3-5-sonnet-20241022",
     "training_cutoff": "2025-06-01T00:00:00Z"},
    {"name": "treatment", "platform": "codex",
     "model": "openai/gpt-4o-2024-08-06",
     "training_cutoff": "2025-07-01T00:00:00Z"},
]


def _dated_corpus(created_at="2026-01-01T00:00:00Z"):
    m = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha=_SHA, status="admitted",
                         created_at=created_at) for i in range(5)],
    )
    m.calibration.status = "full-run-validated"
    return m


def _official_ready(tmp_path, ctx, **overrides):
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx, **overrides)
    _populate(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    _seed_full_calibration(ledger, ctx)
    return spec, ledger


def _probe(ledger, ctx, spec, responses):
    """One probe over task0 (canary-bearing) for both arms, scripted per arm."""
    run_memory_probe(
        ledger, ctx, arms=spec.arms,
        tasks=[ProbeTask(task_id="task0", task_sha=_SHA,
                         prompt="restore the widget cache safely", has_canary=True)],
        provider=FakeProvider(responses),
    )


def test_ac5_asymmetry_refuses_official(tmp_path):
    """One arm's model flagged on a task, the other clean by date: the fence
    refuses the official render, naming the task and the arms; exploratory
    still renders, watermarked, with the summary [AC-5, D001]."""
    ctx = fixed_ctx()
    spec, ledger = _official_ready(tmp_path, ctx, arms=_ARMS_WITH_CUTOFFS)
    canary = derive_canary(_SHA)
    # control regurgitates the canary; treatment does not
    _probe(ledger, ctx, spec, [f"…continues <!-- {canary} -->", "no idea"])
    _seed_matching_selfcheck(ledger, ctx, spec)
    manifest = _dated_corpus()
    findings = compute_findings(ledger, spec, spec.seed, corpus_manifest=manifest, **_FAST)

    assert findings.contamination["per_arm"]["control"]["flagged_task_ids"] == ["task0"]
    assert findings.contamination["per_arm"]["treatment"]["flagged"] == 0
    assert findings.contamination["asymmetric"] == [
        {"task_id": "task0", "flagged_arms": ["control"], "unflagged_arms": ["treatment"]}
    ]
    with pytest.raises(AsymmetricContaminationError) as exc:
        render_markdown(findings, ledger, "official", corpus_manifest=manifest)
    msg = str(exc.value)
    assert "task0" in msg and "control" in msg and "treatment" in msg
    assert cant_analyze_reason(exc.value) is CantAnalyzeReason.asymmetric_contamination

    # exploratory always renders, watermarked, with the summary [AC-5]
    md = render_markdown(findings, ledger, "exploratory")
    assert "EXPLORATORY" in md
    assert "Contamination (disclosed, non-suppressing)" in md
    assert "ASYMMETRIC" in md and "task0" in md


def test_ac5_symmetric_discloses(tmp_path):
    """Symmetric flagged contamination degrades both arms equally: the official
    render succeeds and carries the disclosed, non-suppressing caveat [AC-5]."""
    ctx = fixed_ctx()
    spec, ledger = _official_ready(tmp_path, ctx, arms=_ARMS_WITH_CUTOFFS)
    canary = derive_canary(_SHA)
    # BOTH arms regurgitate — symmetric
    _probe(ledger, ctx, spec,
           [f"…continues <!-- {canary} -->", f"training doc: <!-- {canary} -->"])
    _seed_matching_selfcheck(ledger, ctx, spec)
    manifest = _dated_corpus()
    findings = compute_findings(ledger, spec, spec.seed, corpus_manifest=manifest, **_FAST)

    assert findings.contamination["asymmetric"] == []
    md = render_markdown(findings, ledger, "official", corpus_manifest=manifest)
    assert "Official findings" in md
    assert "Contamination (disclosed, non-suppressing)" in md
    assert "symmetric flagged contamination" in md
    assert "flagged_task_ids=['task0']" in md


def test_all_unknown_renders_official_with_caveat(tmp_path):
    """No dates, no probe: every (task, arm) pair is honestly unknown — the
    official render succeeds with the unknown caveat, never upgraded to clean
    [AC-5, AC-1 constraint]."""
    ctx = fixed_ctx()
    spec, ledger = _official_ready(tmp_path, ctx)  # default arms: no cutoffs
    _seed_matching_selfcheck(ledger, ctx, spec)
    # eval6's undated corpus: no created_at anywhere
    from tests.test_eval6_analyze import _full_corpus

    manifest = _full_corpus()
    findings = compute_findings(ledger, spec, spec.seed, corpus_manifest=manifest, **_FAST)
    c = findings.contamination
    assert c["probe_status"] == "not_run"
    assert all(s["clean_by_date"] == 0 and s["flagged"] == 0 for s in c["per_arm"].values())
    md = render_markdown(findings, ledger, "official", corpus_manifest=manifest)
    assert "unknown is" in md and "never upgraded to clean" in md


def test_cant_probe_status_disclosed(tmp_path):
    """A fail-closed probe is disclosed as cant_probe(reason) in the summary —
    a failed probe never silently reads as a clean one."""
    from harness.judge.providers.base import ProviderTimeout

    ctx = fixed_ctx()
    spec, ledger = _official_ready(tmp_path, ctx)
    run_memory_probe(
        ledger, ctx, arms=spec.arms,
        tasks=[ProbeTask(task_id="task0", task_sha=_SHA, prompt="p", has_canary=True)],
        provider=FakeProvider([ProviderTimeout("deadline")]),
    )
    _seed_matching_selfcheck(ledger, ctx, spec)
    from tests.test_eval6_analyze import _full_corpus

    findings = compute_findings(
        ledger, spec, spec.seed, corpus_manifest=_full_corpus(), **_FAST
    )
    assert findings.contamination["probe_status"] == "cant_probe(timeout)"
    md = render_markdown(findings, ledger, "exploratory")
    assert "cant_probe(timeout)" in md
