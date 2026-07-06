"""OTLP normalization golden pairs [refactor 10 §4, §6.1, §7].

The committed ``tests/fixtures/otlp/*.spans.json`` inputs paired with their
byte-exact ``*.trajectory.json`` / ``*.flight_recorder.json`` outputs ARE the
normative mapping spec — reviewed before the projection is trusted (the [refactor
01] golden discipline). This module holds the adapter-free half: the committed
goldens are valid FROZEN v3 records, and the adversarial golden's emitted bytes
carry none of the identity strings its input laces through non-whitelisted
attributes (§5, the whitelist-is-a-property made executable on the committed
artifact). The adapter half (byte-exact reproduction, the ``OTLP_MAPPING_VERSION``
drift pin, closed-vocabulary, determinism, honest absence) lives beside it once
``harness/adapters/otlp.py`` lands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.run.flight_recorder import (
    FLIGHT_RECORDER_SCHEMA_VERSION,
    parse_flight_recorder,
)
from harness.run.trajectory import TRAJECTORY_SCHEMA_VERSION, parse_trajectory
from tests.fixtures.otlp.regen import ADVERSARIAL_IDENTITY_STRINGS, FIXTURE_DIR, FIXTURES

_TRAJECTORY_GOLDENS = sorted(FIXTURE_DIR.glob("*.trajectory.json"))
_FLIGHT_GOLDENS = sorted(FIXTURE_DIR.glob("*.flight_recorder.json"))


def test_every_fixture_has_an_input_and_a_trajectory_golden():
    """The corpus is present and each fixture has at least an input + trajectory
    golden — a deleted fixture cannot silently shrink the mapping's coverage."""
    inputs = {p.name.removesuffix(".spans.json") for p in FIXTURE_DIR.glob("*.spans.json")}
    assert inputs == set(FIXTURES), (inputs, set(FIXTURES))
    trajectories = {p.name.removesuffix(".trajectory.json") for p in _TRAJECTORY_GOLDENS}
    # every fixture produces a trajectory (each corpus entry has ≥1 action step)
    assert trajectories == set(FIXTURES), (trajectories, set(FIXTURES))


@pytest.mark.parametrize("golden", _TRAJECTORY_GOLDENS, ids=lambda p: p.stem)
def test_trajectory_golden_is_a_valid_frozen_v3_record(golden: Path):
    """A committed trajectory golden parses under the FROZEN model — so every
    ``kind`` is in the closed enum and every ``agent`` in the closed vocabulary
    (the model validators are the oracle), and the schema stays pinned at v3."""
    record = parse_trajectory(golden.read_bytes())
    # literal pin, deliberately not the constant: this projection maps into
    # existing fields ONLY, so a version bump here would be a contract break.
    assert record.schema_version == 3 == TRAJECTORY_SCHEMA_VERSION
    assert record.platform == "otlp"
    assert record.steps, f"{golden.name} has no steps"


@pytest.mark.parametrize("golden", _FLIGHT_GOLDENS, ids=lambda p: p.stem)
def test_flight_recorder_golden_is_a_valid_frozen_v3_record(golden: Path):
    """A committed flight-recorder golden parses under the FROZEN model; the schema
    stays pinned at v3, and ``turn`` indices are valid 0-based (or null)."""
    record = parse_flight_recorder(golden.read_bytes())
    assert record.schema_version == 3 == FLIGHT_RECORDER_SCHEMA_VERSION
    assert record.platform == "otlp"
    assert record.entries, f"{golden.name} has no entries"


def test_adversarial_golden_bytes_carry_no_identity():
    """§5: the adversarial fixture laces model ids, vendor names, and arm-name
    strings through non-whitelisted attributes; the committed emitted bytes must
    contain NONE of them. Guarded to be meaningful: the INPUT spans do carry every
    laced string, so a whitelist regression would be caught, not masked."""
    spans = (FIXTURE_DIR / "adversarial.spans.json").read_bytes()
    in_input = [s for s in ADVERSARIAL_IDENTITY_STRINGS if s.encode() in spans]
    assert set(in_input) == set(ADVERSARIAL_IDENTITY_STRINGS), "fixture no longer laces all identity"

    emitted = (FIXTURE_DIR / "adversarial.trajectory.json").read_bytes()
    emitted += (FIXTURE_DIR / "adversarial.flight_recorder.json").read_bytes()
    leaked = [s for s in ADVERSARIAL_IDENTITY_STRINGS if s.encode() in emitted]
    assert leaked == [], f"identity leaked through the projection: {leaked}"


def test_goldens_are_canonical_bytes():
    """Each golden is exactly the canonical serialization its record round-trips to
    — so the committed bytes are the artifact, not a pretty-printed re-derivation
    (the sha the run seam would ledger)."""
    from harness.run.flight_recorder import canonical_bytes as fr_canonical
    from harness.run.trajectory import canonical_bytes as traj_canonical

    for golden in _TRAJECTORY_GOLDENS:
        raw = golden.read_bytes()
        assert traj_canonical(parse_trajectory(raw)) == raw, golden.name
    for golden in _FLIGHT_GOLDENS:
        raw = golden.read_bytes()
        assert fr_canonical(parse_flight_recorder(raw)) == raw, golden.name
