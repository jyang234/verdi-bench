"""Phase 4 exit — a complete fake-engine experiment end-to-end through bench verbs.

plan → run → grade → judge → review build → review record → review reveal →
process score → process record → analyze, using ONLY the ``bench`` CLI (no
test-only kwargs). Asserts the Phase-4 connective tissue holds: judge calibration
and process reporting appear in the rendered findings; reveal discloses the REAL
arm identities from the recorded map; guess accuracy is a measured number; and the
power gate ran at the design's real N. Admission reachability and the
is_schedulable gate are asserted in the second test.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.ledger.query import find_events
from tests.fixtures.builders import write_experiment_yaml

runner = CliRunner()

_FAKE_JUDGE = {
    "model": "fake/deterministic-2026-01-01",
    "rubric": "rubric.md",
    "orders": "both",
    "temperature": 0,
    "escalation": {"kappa_threshold": 0.6, "min_human_verdicts": 1},
}


def _ok(*args):
    r = runner.invoke(app, list(args))
    assert r.exit_code == 0, f"{args}\n{r.output}"
    return r


def test_phase4_exit_pipeline_through_bench_verbs(tmp_path):
    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE), repetitions=1)
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "solve it", "task_class": "refactor"}]}),
        encoding="utf-8",
    )
    ledger = expdir / "ledger.ndjson"

    # plan → run (fake) — one comparison: control vs treatment, repetition 0
    _ok("plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger))
    _ok("run", str(expdir))

    # stand in for the grader container output: control passes its holdout,
    # treatment fails — a real, decisive comparison
    trials = {(r["arm"], r["repetition"]): r for r in
              (ev["trial_record"] for ev in find_events(ledger, "trial"))}
    for (arm, _rep), rec in trials.items():
        ws = Path(rec["artifacts_path"]).parent
        result = "pass" if arm == "control" else "fail"
        (ws / "holdout_results.json").write_text(
            json.dumps({"assertions": [{"id": "h1", "result": result}]}), encoding="utf-8"
        )
    _ok("grade", str(expdir), "--runner", "local")

    # judge → review build → record → reveal
    _ok("judge", str(expdir))
    _ok("review", "build", str(expdir))
    built = find_events(ledger, "review_packet_built")[0]
    cid = built["comparison_id"]
    resp1_arm = built["response_map"]["1"]
    _ok("review", "record", str(expdir), "--comparison-id", cid, "--winner", "1",
        "--arm-recognized", "--arm-guess", resp1_arm)
    _ok("review", "reveal", str(expdir), "--comparison-id", cid)

    # process score (judge) → process record (human, post-reveal) for control's trial
    _ok("process", "score", str(expdir))
    control_trial = trials[("control", 0)]["trial_id"]
    from harness.process.rubric import default_rubric
    scores_file = expdir / "human_scores.json"
    scores_file.write_text(json.dumps({d: 4 for d in default_rubric().dimension_ids}), encoding="utf-8")
    _ok("process", "record", str(expdir), "--trial-id", control_trial,
        "--comparison-id", cid, "--scores", str(scores_file))

    # analyze (exploratory) — the rendered findings
    _ok("analyze", str(expdir), "--exploratory")
    md = (expdir / "findings.exploratory.md").read_text(encoding="utf-8")

    # 1) reveal discloses the REAL arm identities (from the recorded map)
    reveal = find_events(ledger, "reveal")[0]
    assert set(reveal["revealed"]["arm_identities"].values()) == {"control", "treatment"}
    assert reveal["revealed"]["arm_identities"] == built["response_map"]

    # 2) guess accuracy is a MEASURED number: actual_arm supplied from the map
    hv = find_events(ledger, "human_verdict")[0]
    assert hv["integrity"]["actual_arm"] == resp1_arm
    assert hv["integrity"]["arm_guess"] == resp1_arm  # a correct, measured guess

    # 3) judge calibration (per-class kappa) appears in the rendered findings
    assert "Judge calibration (per class)" in md
    assert "refactor: kappa=" in md

    # 4) process reporting (kappa / correlations / style_only) appears
    assert "Process diagnostics" in md
    assert "judge↔human agreement" in md
    assert "score-vs-telemetry correlation" in md

    # 5) the power gate ran at the design's real N (repetitions × corpus size = 1)
    locked = find_events(ledger, "experiment_locked")[0]
    assert locked["mde"]["n_tasks"] == 1

    # the whole ledger still verifies after the full pipeline
    _ok("verify-chain", str(ledger))


def test_phase4_exit_admission_reachable_and_schedulable_gate(tmp_path):
    """Admission is reachable via bench corpus admit (emitting task_admitted), and
    bench run refuses a non-admitted task via is_schedulable."""
    _CURATOR_PRIV = "57d8af6bd26b16f1f558e600e70fb2a40a5349804c864b3513b12015dc155556"
    _CURATOR_PUB = "54f22d27057d6c0a336de3f2d0df143546f31591c169072e90f18f651e49e148"

    # --- admission reachable through the corpus verbs ---
    manifest = CorpusManifest(corpus_id="internal-x", semver="1.0.0", kind="internal",
                              boundary_path=str(tmp_path / "b"))
    mpath = tmp_path / "manifest.json"
    manifest.save(mpath)
    mr = tmp_path / "mr.json"
    mr.write_text(json.dumps({"parent_sha": "a" * 40, "files": []}), encoding="utf-8")
    ticket = tmp_path / "t.txt"
    ticket.write_text("do it", encoding="utf-8")
    out = tmp_path / "cand.json"
    _ok("corpus", "mine", str(mr), "--ticket", str(ticket), "--out", str(out),
        "--miner", "bob", "--manifest", str(mpath), "--task-id", "cand-x")
    sha = CorpusManifest.load(mpath).task("cand-x").sha

    keyfile = tmp_path / "alice.key"
    keyfile.write_text(_CURATOR_PRIV, encoding="utf-8")
    keyring = tmp_path / "keyring.json"
    keyring.write_text(json.dumps([_CURATOR_PUB]), encoding="utf-8")
    expdir = tmp_path / "exp"
    expdir.mkdir()
    ledger = expdir / "ledger.ndjson"
    _ok("corpus", "approve", str(expdir), "--candidate-id", "cand-x", "--task-sha", sha,
        "--signing-key", str(keyfile), "--approver", "alice")
    from harness.ledger.events import record_flake_baseline
    from tests.fixtures.builders import fixed_ctx
    record_flake_baseline(ledger, fixed_ctx(), task_id="cand-x", task_sha=sha, k=5,
                          results=[{"run": i, "passed": True} for i in range(5)], verdict="clean")
    _ok("corpus", "admit", str(expdir), "--manifest", str(mpath), "--candidate-id", "cand-x",
        "--task-sha", sha, "--baseline-ref", "b1", "--keyring", str(keyring))
    assert len(find_events(ledger, "task_admitted")) == 1

    # --- bench run refuses a non-admitted task ---
    rundir = tmp_path / "run"
    rundir.mkdir()
    write_experiment_yaml(rundir / "experiment.yaml")
    (rundir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}),
                                       encoding="utf-8")
    rledger = rundir / "ledger.ndjson"
    _ok("plan", str(rundir / "experiment.yaml"), "--ledger", str(rledger))
    pending = CorpusManifest(corpus_id="public-mini", semver="1.0.0", kind="public",
                             tasks=[TaskEntry(task_id="t1", sha="x" * 64, status="pending-curation")])
    pmpath = rundir / "manifest.json"
    pending.save(pmpath)
    _ok("run", str(rundir), "--corpus-manifest", str(pmpath))
    assert find_events(rledger, "trial") == []
    assert any(e["reason"] == "not_schedulable" for e in find_events(rledger, "trial_infra_failed"))
