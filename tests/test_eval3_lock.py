"""EVAL-3 AC-2 / AC-4 — lock genesis, mutation refusal, underpowered ack."""

from __future__ import annotations

import pytest

from harness.ledger import events
from harness.ledger.query import find_events, read_events
from harness.plan.lock import (
    AlreadyLockedError,
    LockMismatchError,
    UnderpoweredError,
    assert_lock,
    lock_experiment,
)
from harness.plan.power import AssumedVariance
from tests.fixtures.builders import fixed_ctx, write_experiment_yaml

# small sim params keep the power check fast in tests
FAST = dict(n_sim=30, n_boot=80, deltas=[0.1, 0.2, 0.3, 0.4, 0.5])


def test_ac2_lock_genesis(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    outcome = lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    locks = find_events(ledger, events.EXPERIMENT_LOCKED)
    assert len(locks) == 1
    assert locks[0]["spec_sha256"] == outcome.spec_sha256
    assert locks[0]["prev_hash"] == "0" * 64  # genesis
    assert "attestation" in locks[0]


def test_ac2_assert_lock_passes_when_unchanged(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    ev = assert_lock(spec, ledger)
    assert ev["event"] == "experiment_locked"


def test_pl1_power_at_real_n(tmp_path):
    """PL-1 + D-P5-4: with a task source, power is computed at the corpus's real
    task-*cluster* count with ``repetitions`` correlated reps per task, not the
    variance source's default n_tasks=50.

    Phase 5 5A changed the model from a flat ``repetitions × corpus size``
    observation count to task clustering: ``n_tasks`` is now the cluster count
    (4 tasks) and ``repetitions`` (3) rides alongside, because correlated reps are
    not independent observations."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml", repetitions=3)
    ledger = tmp_path / "ledger.ndjson"
    task_dicts = [{"id": f"t{i}", "prompt": "p"} for i in range(4)]
    outcome = lock_experiment(spec, ledger, ctx=fixed_ctx(), task_dicts=task_dicts, **FAST)
    assert outcome.mde["n_tasks"] == 4  # 4 task clusters, not 50 and not a flat 12
    assert outcome.mde["repetitions"] == 3  # reps ride alongside the cluster count


def test_pl1_gate_skip_flagged(tmp_path):
    """PL-1: omitting hypothesized_effect skips the power gate — the skip is
    ledgered as a flag, not a silent no-check."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")  # no hypothesized_effect
    ledger = tmp_path / "ledger.ndjson"
    outcome = lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    assert "power_gate_skipped" in outcome.mde["flags"]
    locked = find_events(ledger, events.EXPERIMENT_LOCKED)[0]
    assert "power_gate_skipped" in locked["mde"]["flags"]


def test_pl1_gate_not_skipped_when_effect_present(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml", hypothesized_effect=0.3)
    ledger = tmp_path / "ledger.ndjson"
    outcome = lock_experiment(spec, ledger, ctx=fixed_ctx(),
                              acknowledge_underpowered=True, **FAST)
    assert "power_gate_skipped" not in outcome.mde["flags"]


def test_pl5_bench_plan_uses_calibration_manifest(tmp_path):
    """PL-5: bench plan --corpus-manifest feeds calibration variance into the
    power gate, so a calibrated lock is NOT flagged assumption_based_mde."""
    import json

    from typer.testing import CliRunner

    from harness.cli import app
    from harness.corpus.registry import Calibration, CorpusManifest

    runner = CliRunner()
    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml")
    (expdir / "tasks.yaml").write_text(
        json.dumps({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8"
    )
    ledger = expdir / "ledger.ndjson"
    manifest = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        calibration=Calibration(status="full-run-validated",
                                runs=[{"p": 0.55, "rho": 0.28, "n_tasks": 60, "kind": "full"}]),
    )
    mpath = expdir / "manifest.json"
    manifest.save(mpath)

    r = runner.invoke(app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger),
                            "--corpus-manifest", str(mpath)])
    assert r.exit_code == 0, r.output
    locked = find_events(ledger, events.EXPERIMENT_LOCKED)[0]
    # a real calibration variance was used -> not assumption-based
    assert "assumption_based_mde" not in locked["mde"]["flags"]
    assert locked["mde"]["p"] == 0.55 and locked["mde"]["rho"] == 0.28


def test_ac2_mutation_refused(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    # mutate the yaml after lock
    spec.write_text(spec.read_text() + "\n# tampered\n")
    with pytest.raises(LockMismatchError):
        assert_lock(spec, ledger)


def test_ac4_mde_computed(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    outcome = lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    assert outcome.mde["method"] == "paired_binary_bootstrap_sim"
    # assumed variance ⇒ flag rides into the lock event (this fixture also omits
    # hypothesized_effect, so the power gate is skipped-and-flagged too [PL-1])
    assert "assumption_based_mde" in outcome.mde["flags"]
    ledgered_flags = find_events(ledger, events.EXPERIMENT_LOCKED)[0]["mde"]["flags"]
    assert "assumption_based_mde" in ledgered_flags
    assert "power_gate_skipped" in ledgered_flags


def test_ac4_underpowered_requires_ack(tmp_path):
    # tiny hypothesized effect below any reasonable MDE ⇒ refuse without ack
    spec = write_experiment_yaml(tmp_path / "experiment.yaml", hypothesized_effect=0.001)
    ledger = tmp_path / "ledger.ndjson"
    with pytest.raises(UnderpoweredError):
        lock_experiment(
            spec,
            ledger,
            ctx=fixed_ctx(),
            variance_source=AssumedVariance(p=0.5, rho=0.3, n_tasks=20),
            **FAST,
        )
    # no lock written
    assert find_events(ledger, events.EXPERIMENT_LOCKED) == []


def test_ac4_incomputable_mde_is_underpowered(tmp_path):
    # regression: when MDE can't be computed (no swept effect reaches power), the
    # guard must NOT fail open — a design with a hypothesized effect is refused
    spec = write_experiment_yaml(tmp_path / "experiment.yaml", hypothesized_effect=0.2)
    ledger = tmp_path / "ledger.ndjson"
    # tiny N + tiny deltas ⇒ power never reaches target ⇒ mde None
    with pytest.raises(UnderpoweredError):
        lock_experiment(
            spec, ledger, ctx=fixed_ctx(),
            variance_source=AssumedVariance(p=0.5, rho=0.3, n_tasks=4),
            n_sim=20, n_boot=60, deltas=[0.001, 0.002],
        )
    assert find_events(ledger, events.EXPERIMENT_LOCKED) == []


def test_ac4_ack_ledgered(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml", hypothesized_effect=0.001)
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(
        spec,
        ledger,
        ctx=fixed_ctx(),
        variance_source=AssumedVariance(p=0.5, rho=0.3, n_tasks=20),
        acknowledge_underpowered=True,
        **FAST,
    )
    # PL-14: the acknowledgment rides inline on the single lock event (one
    # attempted operation ⇒ one event), not a separate second event.
    locks = find_events(ledger, events.EXPERIMENT_LOCKED)
    assert len(locks) == 1
    assert len(read_events(ledger)) == 1
    assert locks[0]["acknowledged_underpowered"]["hypothesized_effect"] == 0.001


def test_lock_reads_spec_once_no_toctou(tmp_path, monkeypatch):
    """PL-2: lock hashes the exact bytes it parsed. The spec file is read once,
    so the recorded sha cannot diverge from the validated content via a race
    (old code read it twice: once to parse, once to hash).
    """
    import pathlib

    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"

    reads = {"n": 0}
    real_read_bytes = pathlib.Path.read_bytes
    real_read_text = pathlib.Path.read_text

    def counting_read_bytes(self):
        if str(self) == str(spec):
            reads["n"] += 1
        return real_read_bytes(self)

    def counting_read_text(self, *a, **k):
        if str(self) == str(spec):
            reads["n"] += 1
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting_read_bytes)
    monkeypatch.setattr(pathlib.Path, "read_text", counting_read_text)

    lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    assert reads["n"] == 1


def test_relock_refused(tmp_path):
    """PL-3: a second lock over the same ledger is refused, not silently appended
    as a second experiment_locked event."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    with pytest.raises(AlreadyLockedError):
        lock_experiment(spec, ledger, ctx=fixed_ctx(), **FAST)
    assert len(find_events(ledger, events.EXPERIMENT_LOCKED)) == 1


def test_lock_is_genesis_on_ack_path(tmp_path):
    """PL-3/PL-14: on the acknowledged-underpowered path the lock is the sole
    genesis event — the acknowledgment rides inline, so prev_hash is all-zeros,
    `assert_lock` keys the true genesis, and exactly one event is appended."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml", hypothesized_effect=0.001)
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(
        spec,
        ledger,
        ctx=fixed_ctx(),
        variance_source=AssumedVariance(p=0.5, rho=0.3, n_tasks=20),
        acknowledge_underpowered=True,
        **FAST,
    )
    all_events = read_events(ledger)
    assert len(all_events) == 1  # PL-14: one attempted operation ⇒ one event
    assert all_events[0]["event"] == "experiment_locked"
    assert all_events[0]["prev_hash"] == "0" * 64  # genesis
    assert all_events[0]["acknowledged_underpowered"]["mde"] is not None


def test_assert_lock_refuses_tampered_chain(tmp_path):
    """PL-6: a rewritten lock line whose recorded sha is forged to match a
    mutated spec must still be refused — ``assert_lock`` verifies the hash chain,
    not just the recorded sha. This is the review's exact attack: mutate
    experiment.yaml *and* rewrite the lock line's spec_sha256 so the naive
    equality check passes.
    """
    import json

    from harness.ledger.chain import canonical_line
    from harness.ledger.query import ChainIntegrityError
    from harness.plan.lock import spec_sha256
    from tests.fixtures.builders import seed_trial_and_grade

    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    ctx = fixed_ctx()
    lock_experiment(spec, ledger, ctx=ctx, **FAST)
    # the lock is genesis; give it a successor so a rewrite of it breaks the chain
    seed_trial_and_grade(ledger, ctx, trial_id="t1", task_id="task-1", arm="arm_a")

    # attacker mutates the spec, then forges the recorded sha to match it
    spec.write_text(spec.read_text() + "\n# tampered\n")
    forged = spec_sha256(spec)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    lock_obj = json.loads(lines[0])
    assert lock_obj["event"] == "experiment_locked"
    lock_obj["spec_sha256"] = forged
    lines[0] = canonical_line(lock_obj)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # the naive sha-equality check would now pass ...
    assert json.loads(ledger.read_text().splitlines()[0])["spec_sha256"] == forged
    # ... but the chain is broken at the successor, so assert_lock must refuse.
    with pytest.raises(ChainIntegrityError):
        assert_lock(spec, ledger)
