"""Fixture-ownership meta-test for the gaming detectors [refactor 06 §3].

The detector registry (``DETECTORS``) makes fixture ownership *enforceable*
rather than customary: every id in ``DETECTOR_IDS`` must ship BOTH a planted
violation that flags it AND a clean near-miss that does not. This sweep merges
the ``DETECTOR_FIXTURES`` pairs declared beside the detector tests — the v1 tier
in ``test_eval11_detectors`` and the step-content tier in
``test_eval16_step_forensics`` — and fails closed if any detector lacks its pair,
so a future detector cannot ship without one.
"""

from __future__ import annotations

import pytest

from harness.forensics.detectors import DETECTOR_IDS, run_detectors
from tests import test_eval11_detectors as v1
from tests import test_eval16_step_forensics as v2

# The planted/clean pair per detector id, swept across both detector suites.
_FIXTURES = {**v1.DETECTOR_FIXTURES, **v2.DETECTOR_FIXTURES}


def _flagged(evidence) -> set[str]:
    return {f["detector"] for f in run_detectors(evidence)}


def test_every_detector_owns_a_planted_and_clean_fixture_pair():
    """Set-equality against the live registry: registering a detector without a
    fixture pair (or leaving an orphan pair) fails this test [refactor 06 §3]."""
    assert set(_FIXTURES) == set(DETECTOR_IDS), (
        "detector fixture pairs and DETECTOR_IDS disagree — every detector must "
        f"own a planted+clean pair: missing={set(DETECTOR_IDS) - set(_FIXTURES)}, "
        f"orphan={set(_FIXTURES) - set(DETECTOR_IDS)}"
    )


@pytest.mark.parametrize("det_id", sorted(DETECTOR_IDS))
def test_planted_flags_and_clean_is_silent(det_id):
    """The planted case flags this detector; the clean near-miss does not."""
    pair = _FIXTURES[det_id]
    assert det_id in _flagged(pair["planted"]), (
        f"planted fixture for {det_id!r} did not flag it"
    )
    assert det_id not in _flagged(pair["clean"]), (
        f"clean fixture for {det_id!r} spuriously flagged it"
    )
