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
    # assumed variance ⇒ flag rides into the lock event
    assert "assumption_based_mde" in outcome.mde["flags"]
    assert find_events(ledger, events.EXPERIMENT_LOCKED)[0]["mde"]["flags"] == [
        "assumption_based_mde"
    ]


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
    assert len(find_events(ledger, events.ACKNOWLEDGED_UNDERPOWERED)) == 1
    assert len(find_events(ledger, events.EXPERIMENT_LOCKED)) == 1


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
    """PL-3: even on the acknowledged-underpowered path the lock is the genesis
    event — it is written before the acknowledgment rider, so its prev_hash is
    all-zeros and `assert_lock` keys the true genesis."""
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
    assert all_events[0]["event"] == "experiment_locked"
    assert all_events[0]["prev_hash"] == "0" * 64  # genesis
    assert all_events[1]["event"] == "acknowledged_underpowered"


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
