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


def test_jd9_bench_judge_is_idempotent(tmp_path):
    """Re-running `bench judge` appends zero new verdicts (7A-4).

    Judging iterated every comparison unconditionally; a second run doubled
    the verdict set, inflating calibration/preference statistics. One verdict
    per comparison, per the verb's contract.
    """
    expdir = tmp_path / "exp"
    ledger = _setup(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment", passed=False)

    r1 = runner.invoke(app, ["judge", str(expdir)])
    assert r1.exit_code == 0, r1.output
    after_first = find_events(ledger, "judge_verdict")
    assert len(after_first) == 1

    r2 = runner.invoke(app, ["judge", str(expdir)])
    assert r2.exit_code == 0, r2.output
    after_second = find_events(ledger, "judge_verdict")
    assert len(after_second) == 1  # zero new verdicts on re-run


def _seed_cant_verdict(ledger, ctx, *, comparison_id, reason):
    from harness.ledger.events import append_verdict

    append_verdict(ledger, ctx, verdict={
        "winner": "CANT_JUDGE", "reason": reason, "evidence": [],
        "confidence": "low", "order_inconsistent": False, "source": "judge",
        "comparison_id": comparison_id,
        "provenance": {
            "judge_model": "fake/j", "rubric_sha256": "x", "packet_sha256": "y",
            "call_ids": [], "orders": "both", "temperature": 0.0, "ts": "t",
        },
    })


def test_m13_transient_cant_judge_is_retried(tmp_path):
    """PRA-M13: a prior CANT_JUDGE(timeout) — the judge could not run — is
    re-attempted on a re-run instead of permanently dropping the comparison."""
    expdir = tmp_path / "exp"
    ledger = _setup(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment", passed=False)
    _seed_cant_verdict(ledger, ctx, comparison_id="cmp-t1-r0", reason="timeout")

    r = runner.invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output
    verdicts = [e["verdict"] for e in find_events(ledger, "judge_verdict")]
    # the transient CANT was retried: a real verdict now exists for the comparison
    reals = [v for v in verdicts if v["comparison_id"] == "cmp-t1-r0" and v["winner"] != "CANT_JUDGE"]
    assert len(reals) == 1


def test_m13_terminal_cant_judge_stays_skipped(tmp_path):
    """PRA-M13: a terminal CANT_JUDGE (deterministic for a fixed packet) is not
    re-attempted — no new verdict is appended for it."""
    expdir = tmp_path / "exp"
    ledger = _setup(expdir)
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment", passed=False)
    _seed_cant_verdict(ledger, ctx, comparison_id="cmp-t1-r0", reason="identity_leak")

    before = len(find_events(ledger, "judge_verdict"))
    r = runner.invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output
    assert len(find_events(ledger, "judge_verdict")) == before  # not retried


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
    EscalationConfig reaches calibration (JD-9).

    The human verdict *disagrees* with the judge (judge A, human B) so the reviewed
    pair carries both categories and kappa is defined (=0) under D-5 — an all-agree
    single item would be degenerate/insufficient regardless of the threshold."""
    from harness.judge.schema import Verdict, VerdictProvenance, Winner
    from harness.ledger.events import append_human_verdict

    judge = dict(_FAKE_JUDGE)
    judge["escalation"] = {"kappa_threshold": 0.6, "min_human_verdicts": 1}
    expdir = tmp_path / "exp"
    ledger = _setup(expdir, judge=judge)
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="tr-a", task_id="t1", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="tr-b", task_id="t1", arm="treatment", passed=False)
    # a human verdict DISAGREEING with the judge (judge A, human B) — a defined,
    # non-degenerate pair (kappa = 0), so the class is sufficient at min=1. It
    # carries an integrity block: RV-8(f) excludes integrity-less human verdicts
    # from the reviewed-kappa set, so a reviewed verdict must have one.
    hv = Verdict(
        winner=Winner.B, reason="disagree",
        evidence=[{"kind": "diff", "response": "B", "hunk": "h"}],
        provenance=VerdictProvenance(judge_model="human", rubric_sha256="human",
            packet_sha256="human", call_ids=["human"], orders="single",
            temperature=0.0, ts="t"),
        source="human", comparison_id="cmp-t1-r0", task_class="refactor",
    )
    append_human_verdict(ledger, ctx, verdict=hv.model_dump(mode="json"),
                         arm_recognized=False)

    r = runner.invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output
    # sufficient at min_human_verdicts=1 (would be insufficient at the default 20);
    # kappa is defined (0.000) and below threshold ⇒ escalate
    assert "class refactor: n=1 kappa=0.000 ESCALATE" in r.output


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
