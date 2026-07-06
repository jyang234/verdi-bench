"""EVAL-8 CO-7/CO-8/D-P4-3 — the corpus admission pipeline through the CLI.

Drives mine → (review) → approve (signed) → admit end to end: the mine→manifest
link, curation review showing holdout CONTENT, an Ed25519-signed curation
approval, and admission verifying the signature against the authorized keyring.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from harness.cli import app
from harness.corpus.registry import CorpusManifest
from harness.ledger.events import record_flake_baseline
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, seed_trial_and_grade
from tests.fixtures.grading import write_holdout_results

runner = CliRunner()

_CURATOR_PRIV = "57d8af6bd26b16f1f558e600e70fb2a40a5349804c864b3513b12015dc155556"
_CURATOR_PUB = "54f22d27057d6c0a336de3f2d0df143546f31591c169072e90f18f651e49e148"


def test_admit_legacy_list_keyring_exits_2_not_traceback(tmp_path):
    """A pre-Phase-7 JSON-list keyring must refuse cleanly (exit 2), not escape
    the `except CorpusError` handler as an uncaught KeyringFormatError traceback
    [D-P7-3]."""
    manifest = CorpusManifest(corpus_id="internal-k", semver="1.0.0", kind="internal",
                              boundary_path=str(tmp_path / "boundary"))
    mpath = tmp_path / "manifest.json"
    manifest.save(mpath)
    keyring = tmp_path / "keyring.json"
    keyring.write_text(json.dumps([_CURATOR_PUB]), encoding="utf-8")  # legacy list format
    expdir = tmp_path / "exp"
    expdir.mkdir()
    r = runner.invoke(app, [
        "corpus", "admit", str(expdir), "--manifest", str(mpath),
        "--candidate-id", "c", "--task-sha", "s" * 64, "--baseline-ref", "b1",
        "--keyring", str(keyring),
    ])
    assert r.exit_code == 2, r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)  # clean exit, no traceback
    assert "list" in (r.output + (r.stderr or "")).lower()  # names the legacy format


def test_co8_mine_approve_admit_cli_flow(tmp_path):
    manifest = CorpusManifest(corpus_id="internal-x", semver="1.0.0", kind="internal",
                              boundary_path=str(tmp_path / "boundary"))
    mpath = tmp_path / "manifest.json"
    manifest.save(mpath)

    mr = tmp_path / "mr.json"
    mr.write_text(json.dumps({
        "parent_sha": "a" * 40,
        "files": [{"path": "tests/test_x.py", "change": "added",
                   "content": "def test_x():\n    assert feature() == 1"}],
    }), encoding="utf-8")
    ticket = tmp_path / "ticket.txt"
    ticket.write_text("Implement feature X", encoding="utf-8")
    out = tmp_path / "cand-x.json"

    # mine -> stage into the manifest as a pending candidate mined by bob (CO-8)
    r = runner.invoke(app, [
        "corpus", "mine", str(mr), "--ticket", str(ticket), "--out", str(out),
        "--miner", "bob", "--manifest", str(mpath), "--task-id", "cand-x",
    ])
    assert r.exit_code == 0, r.output
    staged = CorpusManifest.load(mpath).task("cand-x")
    assert staged is not None and staged.miner == "bob" and staged.status == "pending-curation"
    sha = staged.sha

    # review surfaces holdout CONTENT, not just the path (CO-7)
    rv = runner.invoke(app, ["corpus", "review", str(out)])
    assert rv.exit_code == 0
    assert "assert feature() == 1" in rv.output

    # approve: alice signs the approval with her key (D-P4-3)
    keyfile = tmp_path / "alice.key"
    keyfile.write_text(_CURATOR_PRIV, encoding="utf-8")
    keyring = tmp_path / "keyring.json"
    # D-P7-3: keyring binds approver id -> pubkey
    keyring.write_text(json.dumps({"alice": _CURATOR_PUB}), encoding="utf-8")
    expdir = tmp_path / "exp"
    expdir.mkdir()
    ledger = expdir / "ledger.ndjson"
    ra = runner.invoke(app, [
        "corpus", "approve", str(expdir), "--candidate-id", "cand-x", "--task-sha", sha,
        "--signing-key", str(keyfile), "--approver", "alice",
    ])
    assert ra.exit_code == 0, ra.output

    # a clean flake baseline for the sha, then admit through the CLI
    record_flake_baseline(ledger, fixed_ctx(), task_id="cand-x", task_sha=sha, k=5,
                          results=[{"run": i, "passed": True} for i in range(5)],
                          verdict="clean")
    rad = runner.invoke(app, [
        "corpus", "admit", str(expdir), "--manifest", str(mpath),
        "--candidate-id", "cand-x", "--task-sha", sha, "--baseline-ref", "b1",
        "--keyring", str(keyring),
    ])
    assert rad.exit_code == 0, rad.output
    assert CorpusManifest.load(mpath).is_schedulable("cand-x") is True
    assert len(find_events(ledger, "task_admitted")) == 1


def test_h2_baseline_verb_flow_no_fabrication(tmp_path):
    """F-H2: the full admission flow through CLI verbs only — mine → approve →
    `corpus baseline` → admit — with no direct record_flake_baseline call. The
    verb runs flake_baseline() against the reference-solution tree and ledgers
    the basis, so the prerequisite is executable through the tool's surface."""
    manifest = CorpusManifest(corpus_id="internal-b", semver="1.0.0", kind="internal",
                              boundary_path=str(tmp_path / "boundary"))
    mpath = tmp_path / "manifest.json"
    manifest.save(mpath)
    mr = tmp_path / "mr.json"
    mr.write_text(json.dumps({
        "parent_sha": "a" * 40,
        "files": [{"path": "tests/test_x.py", "change": "added",
                   "content": "def test_x():\n    assert feature() == 1"}],
    }), encoding="utf-8")
    ticket = tmp_path / "ticket.txt"
    ticket.write_text("Implement feature X", encoding="utf-8")
    out = tmp_path / "cand-b.json"
    r = runner.invoke(app, [
        "corpus", "mine", str(mr), "--ticket", str(ticket), "--out", str(out),
        "--miner", "bob", "--manifest", str(mpath), "--task-id", "cand-b",
    ])
    assert r.exit_code == 0, r.output
    sha = CorpusManifest.load(mpath).task("cand-b").sha

    keyfile = tmp_path / "alice.key"
    keyfile.write_text(_CURATOR_PRIV, encoding="utf-8")
    keyring = tmp_path / "keyring.json"
    keyring.write_text(json.dumps({"alice": _CURATOR_PUB}), encoding="utf-8")
    expdir = tmp_path / "exp"
    expdir.mkdir()
    ledger = expdir / "ledger.ndjson"
    ra = runner.invoke(app, [
        "corpus", "approve", str(expdir), "--candidate-id", "cand-b", "--task-sha", sha,
        "--signing-key", str(keyfile), "--approver", "alice",
    ])
    assert ra.exit_code == 0, ra.output

    # the reference-solution tree: holdouts pass deterministically when solved
    ws = tmp_path / "ref-solution"
    ws.mkdir()
    write_holdout_results(ws, True)
    holdouts = tmp_path / "holdouts"
    holdouts.mkdir()
    rb = runner.invoke(app, [
        "corpus", "baseline", str(expdir), "--task-id", "cand-b", "--task-sha", sha,
        "--workspace", str(ws), "--holdouts-dir", str(holdouts), "--runner", "local",
        "--actor", "alice",
    ])
    assert rb.exit_code == 0, rb.output
    (ev,) = find_events(ledger, "flake_baseline")
    assert ev["verdict"] == "clean"
    assert ev["workspace_basis"] == "reference_solution"
    assert ev["k"] == 5 and len(ev["results"]) == 5

    rad = runner.invoke(app, [
        "corpus", "admit", str(expdir), "--manifest", str(mpath),
        "--candidate-id", "cand-b", "--task-sha", sha, "--baseline-ref", "b1",
        "--keyring", str(keyring),
    ])
    assert rad.exit_code == 0, rad.output
    assert CorpusManifest.load(mpath).is_schedulable("cand-b") is True


def test_h2_baseline_verb_quarantines_on_failure(tmp_path):
    """F-H2: a failing run through the verb quarantines (exit 1) and the event
    carries the auditable per-run results."""
    expdir = tmp_path / "exp"
    expdir.mkdir()
    ws = tmp_path / "ref-solution"
    ws.mkdir()
    write_holdout_results(ws, False)
    holdouts = tmp_path / "holdouts"
    holdouts.mkdir()
    r = runner.invoke(app, [
        "corpus", "baseline", str(expdir), "--task-id", "t", "--task-sha", "s" * 64,
        "--workspace", str(ws), "--holdouts-dir", str(holdouts), "--runner", "local",
        "--actor", "alice",
    ])
    assert r.exit_code == 1, r.output
    (ev,) = find_events(expdir / "ledger.ndjson", "flake_baseline")
    assert ev["verdict"] == "quarantined"


def test_h2_baseline_verb_outage_is_inconclusive(tmp_path, monkeypatch):
    """F-H2/GR-8: a grader outage through the verb is inconclusive — non-zero
    exit, NOTHING ledgered, no quarantine."""
    import subprocess

    expdir = tmp_path / "exp"
    expdir.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()

    def daemon_down(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 1, "", "no daemon")

    monkeypatch.setattr(subprocess, "run", daemon_down)
    r = runner.invoke(app, [
        "corpus", "baseline", str(expdir), "--task-id", "t", "--task-sha", "s" * 64,
        "--workspace", str(ws), "--holdouts-dir", str(tmp_path), "--runner", "docker",
        "--actor", "alice",
    ])
    assert r.exit_code == 2, r.output
    assert not (expdir / "ledger.ndjson").exists() or \
        find_events(expdir / "ledger.ndjson", "flake_baseline") == []


def test_h2_baseline_verb_rejects_unknown_runner(tmp_path):
    expdir = tmp_path / "exp"
    expdir.mkdir()
    r = runner.invoke(app, [
        "corpus", "baseline", str(expdir), "--task-id", "t", "--task-sha", "s" * 64,
        "--workspace", str(tmp_path), "--holdouts-dir", str(tmp_path),
        "--runner", "dcoker", "--actor", "alice",
    ])
    assert r.exit_code != 0
    assert "docker or local" in (r.output + (r.stderr or ""))


def test_co4_calibrate_ledgers_run_from_grades(tmp_path):
    """The run-path calibration hook derives p / n_tasks from a completed run's
    grades and ledgers a calibration_run, advancing the manifest [CO-4]."""
    manifest = CorpusManifest(corpus_id="internal-z", semver="1.0.0", kind="internal",
                              boundary_path=str(tmp_path / "boundary"))
    mpath = tmp_path / "manifest.json"
    manifest.save(mpath)

    expdir = tmp_path / "exp"
    expdir.mkdir()
    ledger = expdir / "ledger.ndjson"
    ctx = fixed_ctx(experiment_id="exp")
    # two tasks graded: one passes, one fails -> p = 0.5, n_tasks = 2
    seed_trial_and_grade(ledger, ctx, trial_id="t1", task_id="ta", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="t2", task_id="tb", arm="control", passed=False)

    r = runner.invoke(app, ["corpus", "calibrate", str(expdir), "--manifest", str(mpath),
                            "--kind", "full", "--rho", "0.3"])
    assert r.exit_code == 0, r.output
    runs = find_events(ledger, "calibration_run")
    assert len(runs) == 1
    assert runs[0]["run"]["n_tasks"] == 2
    assert abs(runs[0]["run"]["p"] - 0.5) < 1e-9
    # the saved manifest carries the run (what bench plan's variance loader reads)
    m = CorpusManifest.load(mpath)
    assert m.calibration.status == "full-run-validated"
    assert m.calibration.runs[-1]["n_tasks"] == 2


def test_co7_admit_rejects_self_approval_cli(tmp_path):
    """bob mines and bob approves -> admission refuses at the CLI (exit 2)."""
    manifest = CorpusManifest(corpus_id="internal-y", semver="1.0.0", kind="internal",
                              boundary_path=str(tmp_path / "boundary"))
    mpath = tmp_path / "manifest.json"
    manifest.save(mpath)
    mr = tmp_path / "mr.json"
    mr.write_text(json.dumps({"parent_sha": "a" * 40, "files": []}), encoding="utf-8")
    ticket = tmp_path / "t.txt"
    ticket.write_text("do it", encoding="utf-8")
    out = tmp_path / "cand.json"
    assert runner.invoke(app, [
        "corpus", "mine", str(mr), "--ticket", str(ticket), "--out", str(out),
        "--miner", "bob", "--manifest", str(mpath), "--task-id", "cand-y",
    ]).exit_code == 0
    sha = CorpusManifest.load(mpath).task("cand-y").sha

    keyfile = tmp_path / "bob.key"
    keyfile.write_text(_CURATOR_PRIV, encoding="utf-8")
    keyring = tmp_path / "keyring.json"
    # D-P7-3: bob is an authorized approver, but he is also the miner ⇒ self-approval
    keyring.write_text(json.dumps({"bob": _CURATOR_PUB}), encoding="utf-8")
    expdir = tmp_path / "exp"
    expdir.mkdir()
    ledger = expdir / "ledger.ndjson"
    # bob (the miner) approves — signs as "bob"
    assert runner.invoke(app, [
        "corpus", "approve", str(expdir), "--candidate-id", "cand-y", "--task-sha", sha,
        "--signing-key", str(keyfile), "--approver", "bob",
    ]).exit_code == 0
    record_flake_baseline(ledger, fixed_ctx(), task_id="cand-y", task_sha=sha, k=5,
                          results=[{"run": i, "passed": True} for i in range(5)],
                          verdict="clean")
    rad = runner.invoke(app, [
        "corpus", "admit", str(expdir), "--manifest", str(mpath),
        "--candidate-id", "cand-y", "--task-sha", sha, "--baseline-ref", "b1",
        "--keyring", str(keyring),
    ])
    assert rad.exit_code == 2
    assert CorpusManifest.load(mpath).is_schedulable("cand-y") is False
