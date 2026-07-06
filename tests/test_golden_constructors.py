"""Constructor-replay serialization golden [refactor 01 §1 item 2].

Every typed constructor in ``harness.ledger.events`` is invoked with fixed
context and representative payloads (each omit-if-None field both ways, each
always-present-nullable field both null and non-null); the emitted lines must
be byte-identical to ``tests/fixtures/data/golden_constructors.ndjson``. This
is the enabling gate for the declarative event registry ([refactor 06] §2):
after that conversion the same replay must produce the same bytes.

Commit-independence: constructors stamp ``instrument {version, git_sha}`` from
``harness.version`` (the CURRENT checkout HEAD). The replay pins identity via
``goldens.pin_instrument``, so these tests pass regardless of HEAD — asserted
below by checking no live sha reached the committed bytes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness.ledger.chain import verify_chain
from harness.ledger.events import REGISTERED_EVENTS
from tests.fixtures import goldens

_DATA = Path(__file__).parent / "fixtures" / "data"
_CONSTRUCTORS = _DATA / "golden_constructors.ndjson"


def _committed_events() -> list[dict]:
    return [
        json.loads(line)
        for line in _CONSTRUCTORS.read_bytes().split(b"\n")
        if line.strip()
    ]


def test_replay_reproduces_committed_bytes(tmp_path):
    replay = tmp_path / "replay.ndjson"
    replayed = goldens.build_constructor_replay(replay)
    assert replay.read_bytes() == _CONSTRUCTORS.read_bytes(), (
        "constructor replay drifted from the committed golden — an event "
        "constructor or the chain canonicalization changed its bytes "
        "(see tests/fixtures/data/regen_goldens.py before touching the fixture)"
    )
    assert replayed == REGISTERED_EVENTS


def test_committed_fixture_covers_every_registered_event_type():
    """Set-equality against the LIVE registry: registering a 32nd event type
    fails this test until the replay (and fixture) are deliberately extended."""
    committed = {ev["event"] for ev in _committed_events()}
    assert committed == REGISTERED_EVENTS


def test_committed_fixture_is_a_verifying_chain():
    result = verify_chain(_CONSTRUCTORS)
    assert result.ok, result.detail


# Omit-if-None constructor parameters (and the gated ``integrity`` block on
# human_verdict): the fixture must contain, per event type, at least one line
# WITH each key and one line WITHOUT it — the "both ways" coverage the golden
# claims [refactor 01 §1 item 2].
_OMIT_IF_NONE_KEYS = {
    "experiment_locked": (
        "task_commitment", "acknowledged_underpowered", "rubric_sha256",
    ),
    "trial": ("trajectory_sha", "flight_recorder_sha"),
    "trial_infra_failed": ("cost",),
    "grade": (
        "fractional_score", "grader", "override_of",
        "workspace_sha256", "workspace_walk_version",
    ),
    "process_score": ("rubric_sha256",),
    "cant_grade": ("override_of",),
    "flake_baseline": ("workspace_basis",),
    "human_verdict": ("integrity",),
    "selfcheck": ("validation_coverage", "validation_n_sim"),
    "findings_rendered": ("multi_arm_correction",),
    "reused_trial": ("diff_sha256",),
}


def test_omit_if_none_fields_are_exercised_both_ways():
    by_type: dict[str, list[dict]] = {}
    for ev in _committed_events():
        by_type.setdefault(ev["event"], []).append(ev)
    for event_type, keys in _OMIT_IF_NONE_KEYS.items():
        lines = by_type[event_type]
        for key in keys:
            present = [ev for ev in lines if key in ev]
            absent = [ev for ev in lines if key not in ev]
            assert present, f"{event_type}: no fixture line carries {key!r}"
            assert absent, f"{event_type}: no fixture line omits {key!r}"


def test_always_present_nullable_fields_are_exercised_both_ways():
    """selfcheck's ``coverage``/``mc_interval`` and the integrity block's
    ``arm_guess``/``actual_arm`` are always present but nullable — the fixture
    pins a null and a non-null occurrence of each."""
    events = _committed_events()
    selfchecks = [ev for ev in events if ev["event"] == "selfcheck"]
    for key in ("coverage", "mc_interval"):
        values = [ev[key] for ev in selfchecks]  # KeyError = shape drift
        assert any(v is None for v in values)
        assert any(v is not None for v in values)
    integrity = [
        ev["integrity"] for ev in events
        if ev["event"] == "human_verdict" and "integrity" in ev
    ]
    for key in ("arm_guess", "actual_arm"):
        values = [block[key] for block in integrity]
        assert any(v is None for v in values)
        assert any(v is not None for v in values)


def test_fixture_is_commit_independent():
    """The trap [refactor 01 §1 item 2]: provenance stamps the CURRENT git sha
    unless pinned. Every committed event must carry the pinned identity, and
    the live checkout sha must appear nowhere in the fixture bytes."""
    for ev in _committed_events():
        assert ev["provenance"]["instrument"] == {
            "version": goldens.PINNED_INSTRUMENT_VERSION,
            "git_sha": goldens.PINNED_INSTRUMENT_GIT_SHA,
        }

    from harness.version import git_sha

    live = git_sha()
    if re.fullmatch(r"[0-9a-f]{40}", live) and live != goldens.PINNED_INSTRUMENT_GIT_SHA:
        data = _CONSTRUCTORS.read_bytes()
        assert live.encode("ascii") not in data
