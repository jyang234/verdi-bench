"""Phase 5 exit — the reproduced statistical-correctness pathologies stay fixed.

Each pathology also has its own failing-then-fixed test in the per-story files
(``test_eval6_analyze`` AN-*, ``test_eval2_*`` JD-*, ``test_eval3_*`` power-N);
this module is the single Phase-5 exit checklist, asserting the headline
corrections hold together on one pipeline and one place documents the exit
criteria as executable assertions.
"""

from __future__ import annotations

import numpy as np
import pytest

from harness.analyze.report import (
    CorpusMismatchError,
    compute_findings,
    render_html,
    render_markdown,
)
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.judge.schema import (
    Confidence,
    Evidence,
    Verdict,
    VerdictProvenance,
    Winner,
)
from harness.ledger.events import append_verdict, record_calibration_run
from harness.plan.power import AssumedVariance, mde_check, simulate_clustered_pair_deltas
from harness.schema.experiment import ExperimentSpec
from harness.schema.judge_config import is_alias_model_id
from tests.fixtures.builders import (
    fixed_ctx,
    locked_experiment,
    seed_trial_and_grade,
    valid_experiment_dict,
)

_FAST = dict(coverage_n_sim=40, n_boot=500)

_PREF_ARMS = [
    {"name": "control", "platform": "claude_code",
     "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
    {"name": "treatment", "platform": "codex", "model": "openai/gpt-4o-2024-08-06", "payload": {}},
    {"name": "challenger", "platform": "codex",
     "model": "openai/gpt-4o-mini-2024-07-18", "payload": {}},
]


def _seed_pref(ledger, ctx, *, cid, task_id, winner, arm_map):
    prov = VerdictProvenance(
        judge_model="fake/judge-1", rubric_sha256="r" * 64, packet_sha256="p" * 64,
        call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t",
    )
    ev = ([] if winner in ("TIE", "CANT_JUDGE")
          else [Evidence(kind="diff", response=winner, hunk="@@")])
    v = Verdict(winner=Winner(winner), reason="x", evidence=ev, provenance=prov,
                comparison_id=cid, task_id=task_id, task_class="cls", arm_map=arm_map)
    append_verdict(ledger, ctx, verdict=v.model_dump(mode="json"))


def test_exit_judge_preference_pooling_fixed(tmp_path):
    """AN-1: a 3-arm judge-preference design no longer feeds the same pooled
    verdicts to every comparison; each pair reports its own arm-mapped sign, and
    CANT_JUDGE/TIE are excluded (not imputed to 0)."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(
        tmp_path / "e", ctx=ctx, arms=_PREF_ARMS,
        primary_metric="judge_preference", decision_rule="delta_judge_preference > 0",
    )
    ct, cc = {"A": "control", "B": "treatment"}, {"A": "control", "B": "challenger"}
    for i in range(4):
        _seed_pref(ledger, ctx, cid=f"ct{i}", task_id=f"t{i}", winner="A", arm_map=ct)
        _seed_pref(ledger, ctx, cid=f"cc{i}", task_id=f"t{i}", winner="B", arm_map=cc)
    _seed_pref(ledger, ctx, cid="tie", task_id="t9", winner="TIE", arm_map=ct)  # excluded
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    by = {cf.label: cf for cf in f.comparisons}
    assert by["control vs treatment"].effect["mean_paired_delta"] == 1.0
    assert by["control vs challenger"].effect["mean_paired_delta"] == -1.0
    assert by["control vs treatment"].n_tasks == 4  # the TIE task is excluded, not imputed
    assert by["control vs treatment"].claim_tag == "judgment"  # AN-6


def test_exit_wrong_corpus_fence_fixed(tmp_path):
    """AN-2: the official fence refuses a corpus that is not the pre-registered one."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)  # public-mini@1.0.0
    for i in range(4):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c{i}", task_id=f"task{i}", arm="control",
                             passed=True, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"t{i}", task_id=f"task{i}", arm="treatment",
                             passed=False, provenance={"image_digest": "d"})
    record_calibration_run(ledger, ctx, corpus_id="public-mini", semver="1.0.0", kind="full",
                           run={"p": 0.5, "rho": 0.3, "n_tasks": 4}, status="full-run-validated")
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    wrong = CorpusManifest(
        corpus_id="totally-different", semver="9.9.9", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha="a" * 64, status="admitted") for i in range(4)],
    )
    wrong.calibration.status = "full-run-validated"
    with pytest.raises(CorpusMismatchError):
        render_markdown(f, ledger, "official", corpus_manifest=wrong)


def test_exit_fabricated_n_coverage_fixed(tmp_path):
    """AN-4: a continuous primary selects its CI method under a continuous null at
    the realized N — the null_model is recorded and is not a binary null at 50."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(
        tmp_path / "e", ctx=ctx, primary_metric="cost_per_task",
        decision_rule="delta_cost_per_task < 0",
    )
    for i in range(6):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c{i}", task_id=f"task{i}", arm="control",
                             telemetry={"cost": 1.0 + 0.1 * i, "wall_time_s": 10.0},
                             provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"t{i}", task_id=f"task{i}", arm="treatment",
                             telemetry={"cost": 1.1 + 0.1 * i, "wall_time_s": 9.0},
                             provenance={"image_digest": "d"})
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    assert f.ci_selection["null_model"] == "paired_continuous"
    assert f.ci_selection["n_tasks"] == 6


def test_exit_clustered_power_not_optimistic():
    """power-N/D-P5-4: correlated reps carry less information than independent
    observations — 10 clusters × 3 correlated reps do not beat 30 independent."""
    d_shared = simulate_clustered_pair_deltas(np.random.default_rng(0), 200, 5, 0.6, 0.4, 1.0)
    d_one = simulate_clustered_pair_deltas(np.random.default_rng(0), 200, 1, 0.6, 0.4, 1.0)
    assert np.array_equal(d_shared, d_one)  # at rho=1, reps add nothing
    spec = ExperimentSpec.from_dict(valid_experiment_dict())
    fast = dict(n_sim=40, n_boot=100, deltas=[0.05, 0.1, 0.2, 0.3, 0.5])
    clustered = mde_check(spec, AssumedVariance(p=0.5, rho=0.6, n_tasks=999),
                          n_tasks=10, repetitions=3, **fast)
    flat = mde_check(ExperimentSpec.from_dict(valid_experiment_dict(repetitions=1)),
                     AssumedVariance(p=0.5, rho=0.6, n_tasks=999), n_tasks=30, repetitions=1, **fast)
    assert clustered.mde is None or (flat.mde is not None and clustered.mde >= flat.mde)


def test_exit_alias_false_passes_fixed():
    """JD-6: a bare dotted-version alias is rejected; a pinned build is accepted."""
    assert is_alias_model_id("google/gemini-1.5-pro") is True
    assert is_alias_model_id("openai/gpt-4.1") is True
    assert is_alias_model_id("google/gemini-1.5-pro-002") is False


def test_exit_script_injection_escaped(tmp_path):
    """AN-5: a <script> in an arm name is escaped in the HTML render."""
    ctx = fixed_ctx()
    evil = "ctl<script>alert(1)</script>"
    arms = [
        {"name": evil, "platform": "claude_code",
         "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
        {"name": "treatment", "platform": "codex", "model": "openai/gpt-4o-2024-08-06", "payload": {}},
    ]
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx, arms=arms)
    for i in range(3):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c{i}", task_id=f"t{i}", arm=evil,
                             passed=True, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"x{i}", task_id=f"t{i}", arm="treatment",
                             passed=False, provenance={"image_digest": "d"})
    f = compute_findings(ledger, spec, spec.seed, **_FAST)
    html_out = render_html(f, ledger, "exploratory")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_exit_confidence_is_enum():
    """JD-12/D-4: verdict confidence is the low|medium|high enum, migrating a
    legacy float to its band."""
    prov = VerdictProvenance(judge_model="fake/j-1", rubric_sha256="a" * 64,
                             packet_sha256="b" * 64, call_ids=["c"], orders="single",
                             temperature=0.0, ts="t")
    ev = [Evidence(kind="diff", response="A", hunk="@@")]
    assert Verdict(winner=Winner.A, reason="x", confidence=0.9,
                   evidence=ev, provenance=prov).confidence is Confidence.high
