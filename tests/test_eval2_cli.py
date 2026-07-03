"""EVAL-2 JD-9 — the ``bench judge`` verb wires the judge end to end.

Before Phase 4 there was no ``judge`` verb (``judge_pair`` had zero production
callers). These tests drive ``bench judge`` through the CLI over graded trials
and assert the verdicts carry a deterministic ``comparison_id``, the A/B→arm
map (D-P4-1), and the ``task_class`` — and that the locked ``EscalationConfig``
reaches calibration (JD-9).
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, seed_trial_and_grade, write_experiment_yaml

runner = CliRunner()

_FAKE_JUDGE = {
    "model": "fake/deterministic-2026-01-01",
    "rubric": "rubric.md",
    "orders": "both",
    "temperature": 0,
}


def _setup(expdir, *, judge=None, tasks=None):
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml", judge=judge or dict(_FAKE_JUDGE))
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    tasks = tasks or [{"id": "t1", "prompt": "solve it", "task_class": "refactor"}]
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)])
    assert r.exit_code == 0, r.output
    return ledger


def test_jd9_bench_judge_verb_judges_graded_comparisons(tmp_path):
    expdir = tmp_path / "exp"
    ledger = _setup(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    # control passes the holdout; treatment fails -> the content-based fake judge
    # prefers control (arm A) consistently across both orders.
    seed_trial_and_grade(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment", passed=False)

    r = runner.invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output
    verdicts = find_events(ledger, "judge_verdict")
    assert len(verdicts) == 1
    v = verdicts[0]["verdict"]
    assert v["comparison_id"] == "cmp-t1-r0"
    assert v["arm_map"] == {"A": "control", "B": "treatment"}
    assert v["task_class"] == "refactor"
    assert v["winner"] == "A"  # control passed, treatment failed
    assert v["single_order"] is False
    # the ledger still verifies after judging
    assert runner.invoke(app, ["verify-chain", str(ledger)]).exit_code == 0


def _seed_trial_with_workspace(ledger, ctx, *, trial_id, task_id, arm, workspace, passed):
    """Seed one trial whose artifacts_path points at a real on-disk workspace, so
    the judge assembles a diff from actual files."""
    from harness.adapters.base import Outcome, Provenance, Telemetry, TrialRecord
    from harness.ledger.events import record_grade, record_trial

    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    rec = TrialRecord.assemble(
        trial_id=trial_id, task_id=task_id, arm=arm, repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(), provenance=Provenance(),
        artifacts_path=str(artifacts),
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    record_grade(ledger, ctx, trial_id=trial_id, task_sha=f"sha-{task_id}",
                 assertions=[{"id": "h1", "source": "holdout_test",
                              "result": "pass" if passed else "fail"}],
                 binary_score=passed)


def test_jd9_canaries_derived_from_spec(tmp_path):
    """The identity firewall is fed from the locked spec: an arm name leaking into
    the judged diff is refused as CANT_JUDGE(identity_leak) with no test-supplied
    canary list."""
    expdir = tmp_path / "exp"
    ledger = _setup(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    ws_a = tmp_path / "wsa"
    ws_a.mkdir()
    # the arm name "treatment" leaks into control's solution file
    (ws_a / "solution.txt").write_text("fix implemented by treatment stack", encoding="utf-8")
    _seed_trial_with_workspace(ledger, ctx, trial_id="tr-a", task_id="t1",
                               arm="control", workspace=ws_a, passed=True)
    _seed_trial_with_workspace(ledger, ctx, trial_id="tr-b", task_id="t1",
                               arm="treatment", workspace=tmp_path / "wsb", passed=False)

    r = runner.invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output
    v = find_events(ledger, "judge_verdict")[0]["verdict"]
    assert v["winner"] == "CANT_JUDGE" and v["reason"] == "identity_leak"


def test_jd9_escalation_config_threaded(tmp_path):
    """A low ``min_human_verdicts`` in the locked escalation block makes a class
    'sufficient' where the hardcoded default 20 would not — proving the
    EscalationConfig reaches calibration (JD-9)."""
    from harness.judge.schema import Verdict, VerdictProvenance, Winner
    from harness.ledger.events import append_human_verdict

    judge = dict(_FAKE_JUDGE)
    judge["escalation"] = {"kappa_threshold": 0.6, "min_human_verdicts": 1}
    expdir = tmp_path / "exp"
    ledger = _setup(expdir, judge=judge)
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment", passed=False)
    # a human verdict agreeing with the judge (winner A) on the same comparison
    hv = Verdict(
        winner=Winner.A, reason="agree",
        evidence=[{"kind": "diff", "response": "A", "hunk": "h"}],
        provenance=VerdictProvenance(judge_model="human", rubric_sha256="human",
            packet_sha256="human", call_ids=["human"], orders="single",
            temperature=0.0, ts="t"),
        source="human", comparison_id="cmp-t1-r0", task_class="refactor",
    )
    append_human_verdict(ledger, ctx, verdict=hv.model_dump(mode="json"))

    r = runner.invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output
    # sufficient at min_human_verdicts=1 (would be insufficient at the default 20)
    assert "class refactor: n=1 kappa=1.000" in r.output


def test_jd11_single_order_flagged_through_verb(tmp_path):
    judge = dict(_FAKE_JUDGE)
    judge["orders"] = "single"
    expdir = tmp_path / "exp"
    ledger = _setup(expdir, judge=judge)
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment", passed=False)
    r = runner.invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output
    v = find_events(ledger, "judge_verdict")[0]["verdict"]
    assert v["single_order"] is True
