"""LedgerView projections — query-parity + the typed-projection semantics [refactor 06 §1].

``LedgerView`` is a strictly-additive read facade: its ``events`` / ``by_kind`` /
``latest`` projections stay in parity with the canonical ``harness.ledger.query``
functions (the invariant that lets consumers migrate onto it opportunistically),
and its typed projections — the sha-hoist reader rule, latest-grade-wins,
quarantine-set membership, verdict keying, and the full per-trial ``trial_story``
join — are pinned as direct semantic assertions. The base parity runs on the
committed golden ledger; the typed projections run on the rich in-memory scenario
(forensics report + quarantine + verdict + verified trajectories) so the semantics
are checked on a real, artifact-backed run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.judge.assemble import comparison_id_for
from harness.ledger import events
from harness.ledger.query import find_events, latest_event, read_events
from harness.ledger.view import LedgerView
from tests.fixtures.scenarios import rich_experiment

_GOLDEN = Path(__file__).parent / "fixtures" / "data" / "golden_ledger.ndjson"


# --- ledgers under test -------------------------------------------------------
@pytest.fixture
def rich(tmp_path):
    return rich_experiment(tmp_path)


_ALL_KINDS = sorted(events.REGISTERED_EVENTS)


# --- by_kind / latest / events: parity with the canonical query functions -----
def test_by_kind_and_latest_match_query_on_golden():
    view = LedgerView(_GOLDEN)
    assert view.events == read_events(_GOLDEN)
    for kind in _ALL_KINDS:
        assert view.by_kind(kind) == find_events(_GOLDEN, kind), kind
        assert view.latest(kind) == latest_event(_GOLDEN, kind), kind


def test_by_kind_and_latest_match_query_on_rich(rich):
    ledger = rich["ledger"]
    view = LedgerView(ledger)
    assert view.events == read_events(ledger)
    for kind in _ALL_KINDS:
        assert view.by_kind(kind) == find_events(ledger, kind), kind
        assert view.latest(kind) == latest_event(ledger, kind), kind


def test_by_kind_is_memoized_same_object():
    view = LedgerView(_GOLDEN)
    assert view.by_kind(events.TRIAL) is view.by_kind(events.TRIAL)


def test_latest_absent_kind_is_none():
    view = LedgerView(_GOLDEN)
    assert view.latest(events.CONTAMINATION_PROBE) is None
    assert view.by_kind(events.CONTAMINATION_PROBE) == []


# --- trials() + the sha-hoist reader rule ------------------------------------
def test_trials_hoist_shas_from_event_not_record(rich):
    """The sha-hoist reader rule [refactor 06 §1]: trials() reads trajectory_sha /
    flight_recorder_sha from the EVENT, never from the embedded trial_record
    (whose sha copies are transport-only and None after a ledger round-trip)."""
    ledger = rich["ledger"]
    view = LedgerView(ledger)
    raw = find_events(ledger, events.TRIAL)
    trials = view.trials()
    assert [tv.trial_id for tv in trials] == [ev["trial_record"]["trial_id"] for ev in raw]
    # non-vacuous discriminator: the fake-engine run verified >=1 trajectory, so
    # >=1 trial EVENT carries a real trajectory_sha while every record's copy is None
    hoisted = [(tv, ev) for tv, ev in zip(trials, raw) if ev.get("trajectory_sha")]
    assert hoisted, "fixture must exercise at least one verified-trajectory trial"
    for tv, ev in hoisted:
        assert tv.trajectory_sha == ev["trajectory_sha"]  # hoisted from the event...
        assert tv.record.get("trajectory_sha") is None    # ...not the record's copy


# --- grade projections: latest-grade-wins ordering ---------------------------
def test_latest_grade_by_trial_is_latest_wins(rich):
    """latest_grade_by_trial returns the LAST grade per trial in ledger order — a
    later (override) grade supersedes the earlier one, while grades_by_trial keeps
    every grade in ledger order [refactor 06 §1]."""
    ledger, ctx = rich["ledger"], rich["ctx"]
    tid = rich["trial_ids"][("t1", "control")]  # the fixture grades this trial False
    assert LedgerView(ledger).latest_grade_by_trial()[tid]["binary_score"] is False
    # a later grade flips the score; a fresh snapshot must prefer it (latest-wins)
    events.record_grade(
        ledger, ctx, trial_id=tid, task_sha="sha-x",
        assertions=[{"id": "h1", "source": "holdout_test", "result": "pass"}],
        binary_score=True,
    )
    view = LedgerView(ledger)
    grades = view.grades_by_trial()[tid]
    assert [g["binary_score"] for g in grades] == [False, True]  # both, in ledger order
    latest = view.latest_grade_by_trial()[tid]
    assert latest is grades[-1]                # the projection returns the LAST...
    assert latest["binary_score"] is True      # ...the override, not the original


def test_rich_projections_are_non_trivial(rich):
    """Guard the oracles are actually exercising the interesting states."""
    view = LedgerView(rich["ledger"])
    assert view.quarantined_trial_ids()  # the fixture quarantines t2/treatment
    assert view.verdicts_by_comparison()  # one advisory verdict on cmp-t1-r0
    assert view.latest(events.FORENSICS_REPORT) is not None


# --- verdict keying + quarantine-set membership ------------------------------
def test_verdict_and_quarantine_projections(rich):
    """quarantined_trial_ids is exactly the set of trials with a ledgered
    forensic_quarantine [D007]; verdicts_by_comparison keys each verdict by its
    comparison_id (the rich fixture's single advisory verdict on cmp-t1-r0)."""
    view = LedgerView(rich["ledger"])
    # quarantine-set membership: exactly the fixture's quarantined trial, no others
    quarantined = rich["trial_ids"][("t2", "treatment")]
    assert view.quarantined_trial_ids() == {quarantined}
    others = {tid for cell, tid in rich["trial_ids"].items() if tid != quarantined}
    assert not (view.quarantined_trial_ids() & others)
    # verdicts keyed by the deterministic comparison id, carrying the verdict body
    cmp_id = comparison_id_for("t1", 0)
    vbc = view.verdicts_by_comparison()
    assert set(vbc) == {cmp_id}
    assert vbc[cmp_id]["winner"] == "B"


def test_provenance_ts_helper():
    view = LedgerView(_GOLDEN)
    ev = view.by_kind(events.TRIAL)[0]
    assert view.provenance_ts(ev) == ev["provenance"]["ts"]
    assert view.provenance_ts({}) is None
    assert view.provenance_ts({"provenance": {}}) is None


# --- trial_story: the full per-trial join ------------------------------------
def test_trial_story_assembles_the_six_correlations(rich):
    """trial_story joins the six per-trial correlations directly off the ledger —
    the record, its deterministic (task, repetition) comparison id, grade/cant-grade
    history, the judge verdicts of THAT comparison, the latest forensic report's
    view, and any quarantine — per-trial, with nulls left null [EVAL-4-D004]."""
    view = LedgerView(rich["ledger"])

    # the flagged control trial: graded, judged, flagged, verified-trajectory,
    # NOT quarantined
    flagged_id = rich["trial_ids"][("t1", "control")]
    flagged = view.trial_story(flagged_id)
    assert flagged is not None
    assert flagged.record["task_id"] == "t1" and flagged.record["arm"] == "control"
    # the comparison id is DERIVED from (task, repetition), never guessed
    assert flagged.comparison_id == comparison_id_for("t1", 0)
    assert [g["binary_score"] for g in flagged.grades] == [False]
    assert flagged.cant_grades == []
    # the verdict of this trial's comparison joins in (winner B on cmp-t1-r0)
    assert [v["winner"] for v in flagged.verdicts] == ["B"]
    # the sha-verified trajectory resolved and its steps rode along
    assert flagged.trajectory_status == "verified"
    assert flagged.trajectory_steps is not None and len(flagged.trajectory_steps) == 3
    assert flagged.flight_recorder_status == "absent"
    assert flagged.flight_recorder_entries is None
    assert flagged.forensics_metrics == {"steps": 3}
    assert [f["trial_id"] for f in flagged.forensics_flags] == [flagged_id]
    assert flagged.quarantine is None

    # the quarantined treatment trial: its own grade, NO verdict on its comparison
    # (the join is per-trial, not bled across trials), and the quarantine disposition
    quar = view.trial_story(rich["trial_ids"][("t2", "treatment")])
    assert quar is not None
    assert quar.comparison_id == comparison_id_for("t2", 0)
    assert [g["binary_score"] for g in quar.grades] == [True]
    assert quar.verdicts == []                       # no verdict on cmp-t2-r0
    assert quar.forensics_metrics is None            # not in the report's metrics
    assert quar.forensics_flags == []
    assert quar.quarantine == {"reason": "fixture quarantine"}


def test_trial_story_unknown_id_is_none():
    view = LedgerView(_GOLDEN)
    assert view.trial_story("no-such-trial") is None


def test_trial_story_rich_flagged_carries_forensics(rich):
    """The flagged trial's story carries the report's metrics + its own flag."""
    view = LedgerView(rich["ledger"])
    story = view.trial_story(rich["flagged"])
    assert story is not None
    assert story.forensics_metrics == {"steps": 3}
    assert [f["trial_id"] for f in story.forensics_flags] == [rich["flagged"]]


# --- verify=True gates on chain integrity ------------------------------------
def test_verify_true_passes_on_intact_golden():
    # constructs without raising; the golden chain verifies
    assert LedgerView(_GOLDEN, verify=True).by_kind(events.TRIAL)


def test_verify_true_refuses_tampered_chain(tmp_path):
    from harness.ledger.query import ChainIntegrityError

    lines = _GOLDEN.read_bytes().splitlines()
    lines[3] = lines[3].replace(b'"binary_score":true', b'"binary_score":false')
    tampered = tmp_path / "ledger.ndjson"
    tampered.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(ChainIntegrityError):
        LedgerView(tampered, verify=True)
    # without verify, construction does not gate (read-only, no assertion)
    assert LedgerView(tampered).by_kind(events.TRIAL)
