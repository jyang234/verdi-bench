"""EVAL-3 AC-2 / AC-4 — lock genesis, mutation refusal, underpowered ack."""

from __future__ import annotations

import pytest

from harness.ledger import events
from harness.ledger.query import find_events
from harness.plan.lock import (
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
