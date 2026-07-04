"""CLI wiring for the contamination sentinel [EVAL-10 AC-2/AC-3, review fixes]."""

from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.contamination.canary import derive_canary, hash_canary
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.ledger.events import record_curation_approval, record_flake_baseline
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, write_experiment_yaml

_CURATOR_PRIV = "57d8af6bd26b16f1f558e600e70fb2a40a5349804c864b3513b12015dc155556"
_CURATOR_PUB = "54f22d27057d6c0a336de3f2d0df143546f31591c169072e90f18f651e49e148"
_SHA = "a1" * 32

_FAKE_ARMS = [
    {"name": "control", "platform": "claude_code", "model": "fake/agent-a", "payload": {}},
    {"name": "treatment", "platform": "codex", "model": "fake/agent-b", "payload": {}},
]


def _admit_fixture(tmp_path, candidate: dict):
    """Experiment dir + ledgered approval/baseline + manifest/keyring/candidate
    files, ready for `bench corpus admit`."""
    from harness.corpus.attestation import sign_approval

    exp = tmp_path / "exp"
    exp.mkdir()
    ledger = exp / "ledger.ndjson"
    ctx = fixed_ctx()
    sig, pk = sign_approval(
        _CURATOR_PRIV, candidate_id="cand-1", task_sha=_SHA, approver="curator"
    )
    record_curation_approval(
        ledger, ctx, candidate_id="cand-1", task_sha=_SHA, approver="curator",
        signature=sig, signer_public_key=pk,
    )
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha=_SHA, k=5,
        results=[{"run": i, "passed": True} for i in range(5)], verdict="clean",
    )
    manifest = CorpusManifest(
        corpus_id="internal-k", semver="1.0.0", kind="internal",
        boundary_path=str(tmp_path / "boundary"),
        tasks=[TaskEntry(task_id="cand-1", sha=_SHA, status="pending-curation",
                         miner="miner-bob")],
    )
    manifest_path = tmp_path / "manifest.json"
    manifest.save(manifest_path)
    keyring_path = tmp_path / "keyring.json"
    keyring_path.write_text(json.dumps({"curator": _CURATOR_PUB}), encoding="utf-8")
    candidate_path = tmp_path / "cand-1.json"
    candidate_path.write_text(
        json.dumps(candidate, sort_keys=True, indent=2), encoding="utf-8"
    )
    return exp, manifest_path, keyring_path, candidate_path


def _admit_args(exp, manifest_path, keyring_path, candidate_path):
    return [
        "corpus", "admit", str(exp), "--manifest", str(manifest_path),
        "--candidate-id", "cand-1", "--task-sha", _SHA, "--baseline-ref", "b1",
        "--keyring", str(keyring_path), "--candidate-json", str(candidate_path),
        "--actor", "tester",
    ]


def test_admit_cli_embeds_alongside_reviewed_bytes(tmp_path):
    """`corpus admit --candidate-json` writes the embedded copy as a sibling
    and leaves the reviewed file byte-identical — the approval sha stays
    verifiable against stored content [AC-2, review fix]."""
    candidate = {"prompt": "Fix the flaky retry loop.", "workspace_ref": "w" * 40}
    exp, manifest_path, keyring_path, candidate_path = _admit_fixture(tmp_path, candidate)
    original_bytes = candidate_path.read_bytes()

    result = CliRunner().invoke(
        app, _admit_args(exp, manifest_path, keyring_path, candidate_path)
    )
    assert result.exit_code == 0, result.output

    canary = derive_canary(_SHA)
    assert candidate_path.read_bytes() == original_bytes  # reviewed bytes intact
    embedded_path = candidate_path.with_suffix(".embedded.json")
    embedded = json.loads(embedded_path.read_text(encoding="utf-8"))
    assert f"<!-- {canary} -->" in embedded["prompt"]
    saved = CorpusManifest.load(manifest_path)
    assert saved.task("cand-1").status == "admitted"
    assert saved.task("cand-1").canary_sha256 == hash_canary(canary)


def test_admit_cli_bad_candidate_refuses_with_nothing_ledgered(tmp_path):
    """A candidate that cannot be embedded refuses admission BEFORE the
    task_admitted event: ledger and manifest stay consistent [review fix]."""
    exp, manifest_path, keyring_path, candidate_path = _admit_fixture(
        tmp_path, {"workspace_ref": "w" * 40}  # no prompt to embed into
    )
    result = CliRunner().invoke(
        app, _admit_args(exp, manifest_path, keyring_path, candidate_path)
    )
    assert result.exit_code == 2, result.output
    assert find_events(exp / "ledger.ndjson", "task_admitted") == []
    assert CorpusManifest.load(manifest_path).task("cand-1").status == "pending-curation"


def _probe_experiment(tmp_path, *, tasks):
    from harness.plan.lock import lock_experiment

    exp = tmp_path / "probe-exp"
    exp.mkdir()
    spec_path = write_experiment_yaml(exp / "experiment.yaml", arms=_FAKE_ARMS)
    (exp / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    # PRA-M2: contamination now gates on the lock like every other stage, so the
    # fixture must pre-register the experiment (lock is the genesis event).
    lock_experiment(
        spec_path, exp / "ledger.ndjson", ctx=fixed_ctx(), n_sim=8, n_boot=40,
        deltas=[0.2, 0.4],
    )
    return exp


def test_probe_cli_ledgers_one_event(tmp_path):
    """`bench contamination probe` runs end to end against the fake provider
    and ledgers exactly one contamination_probe event [AC-3]."""
    exp = _probe_experiment(
        tmp_path, tasks=[{"id": "task0", "prompt": "refactor the retry loop"}]
    )
    result = CliRunner().invoke(
        app,
        ["contamination", "probe", str(exp), "--no-scan-artifacts",
         "--actor", "tester"],
    )
    assert result.exit_code == 0, result.output
    evs = find_events(exp / "ledger.ndjson", "contamination_probe")
    assert len(evs) == 1
    # no canary, no oracle, no scan: honestly unprobed for both arms
    for arm in ("control", "treatment"):
        assert evs[0]["probe"]["arms"][arm]["outcomes"] == {"task0": "unprobed"}


def test_probe_cli_scan_flags_and_echoes_alarm(tmp_path):
    """The scan path: a workspace solution reproducing a holdout flags the
    (arm, task), raises the insulation alarm on stderr, and rides the merged
    probe event [AC-3/AC-4]."""
    from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
    from harness.ledger.events import record_trial

    holdout = (
        "def test_retry_loop_gives_up_after_three_attempts(tmp_path):\n"
        "    loop = RetryLoop(max_attempts=3)\n"
        "    with pytest.raises(GaveUp, match='three'):\n"
        "        loop.run(always_failing_operation, tmp_path)\n"
    )
    exp = _probe_experiment(
        tmp_path,
        tasks=[{"id": "task0", "prompt": "refactor the retry loop",
                "holdouts_dir": "holdouts/task0"}],
    )
    hd = exp / "holdouts" / "task0"
    hd.mkdir(parents=True)
    (hd / "test_retry.py").write_text(holdout, encoding="utf-8")

    ws = tmp_path / "ws-leak"
    (ws / "artifacts").mkdir(parents=True)
    (ws / "solution.py").write_text("# mine\n" + holdout, encoding="utf-8")
    rec = TrialRecord.assemble(
        trial_id="c-1", task_id="task0", arm="control", repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(cost=1.0, wall_time_s=1.0),
        provenance=Provenance(image_digest="d"), flags=Flags(),
        artifacts_path=str(ws / "artifacts"),
    )
    record_trial(exp / "ledger.ndjson", fixed_ctx(), trial_record=rec.model_dump(mode="json"))

    result = CliRunner().invoke(
        app, ["contamination", "probe", str(exp), "--actor", "tester"],
    )
    assert result.exit_code == 0, result.output
    assert "INSULATION ALARM" in result.output
    assert 'flagged=["task0"]' in result.output
    evs = find_events(exp / "ledger.ndjson", "contamination_probe")
    assert evs[0]["probe"]["arms"]["control"]["outcomes"] == {"task0": "flagged"}
    assert evs[0]["probe"]["arms"]["control"]["evidence"]["task0"] == ["solution_overlap"]
