"""EVAL-3 AC-5 — deterministic interleave derivation."""

from __future__ import annotations

from harness.plan.interleave import Trial, derive_schedule, enumerate_trials


def _trials():
    return enumerate_trials(["t1", "t2", "t3"], ["A", "B"], repetitions=2)


def test_ac5_interleave_deterministic():
    trials = _trials()
    s1 = derive_schedule(1234, trials)
    s2 = derive_schedule(1234, trials)
    assert s1 == s2


def test_ac5_seed_changes_order():
    trials = _trials()
    s1 = derive_schedule(1234, trials)
    s2 = derive_schedule(9999, trials)
    assert s1 != s2  # different seed ⇒ different recorded order


def test_ac5_schedule_is_permutation():
    trials = _trials()
    sched = derive_schedule(42, trials)
    assert sorted(t.key() for t in sched) == sorted(t.key() for t in trials)
    assert len(sched) == len(trials) == 3 * 2 * 2


def test_ac5_enumerate_covers_all():
    trials = enumerate_trials(["a", "b"], ["X", "Y", "Z"], repetitions=4)
    assert len(trials) == 2 * 3 * 4
    assert len(set(t.key() for t in trials)) == len(trials)
