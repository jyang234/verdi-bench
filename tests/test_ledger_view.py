"""LedgerView ≡ the hand-rolled joins it replaces [refactor 06 §1].

Every projection is pinned to the exact ``find_events``-based idiom it stands in
for, and the full per-trial ``trial_story`` is pinned to a verbatim port of the
original ``status/trial.py`` scan (the six manual correlations). Both are
checked on the committed Phase-0 golden ledger and on the rich in-memory
scenario (forensics report + quarantine + verdict + verified trajectories), so
the equivalence holds on a real, artifact-backed run, not only the golden.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.judge.assemble import comparison_id_for
from harness.ledger import events
from harness.ledger.query import find_events, latest_event, read_events
from harness.ledger.view import LedgerView, TrialEventView
from harness.run.flight_recorder import resolve_flight_recorder
from harness.run.trajectory import resolve_trajectory
from tests.fixtures.scenarios import rich_experiment

_GOLDEN = Path(__file__).parent / "fixtures" / "data" / "golden_ledger.ndjson"


# --- hand-rolled oracles (the idioms LedgerView replaces) --------------------
def _oracle_latest_grade_by_trial(ledger) -> dict:
    return {e["trial_id"]: e for e in find_events(ledger, events.GRADE)}


def _oracle_grades_by_trial(ledger) -> dict:
    acc: dict[str, list[dict]] = {}
    for e in find_events(ledger, events.GRADE):
        acc.setdefault(e["trial_id"], []).append(e)
    return acc


def _oracle_verdicts_by_comparison(ledger) -> dict:
    acc: dict[str, dict] = {}
    for e in find_events(ledger, events.JUDGE_VERDICT):
        v = e.get("verdict") or {}
        cid = v.get("comparison_id")
        if cid is not None:
            acc[cid] = v
    return acc


def _oracle_quarantined_trial_ids(ledger) -> set:
    return {
        e["forensic_quarantine"]["trial_id"]
        for e in find_events(ledger, events.FORENSIC_QUARANTINE)
    }


def _oracle_trial_detail(ledger, trial_id):
    """A verbatim port of the original status/trial.py:28-132 hand-rolled join."""
    record = None
    trajectory_sha = None
    flight_recorder_sha = None
    grades: list[dict] = []
    cant_grades: list[dict] = []
    verdicts: list[dict] = []
    flags: list[dict] = []
    metrics = None
    quarantine = None

    evs = read_events(ledger)
    for ev in evs:
        kind = ev.get("event")
        if kind == events.TRIAL:
            rec = ev.get("trial_record") or {}
            if rec.get("trial_id") == trial_id:
                record = rec
                trajectory_sha = ev.get("trajectory_sha")
                flight_recorder_sha = ev.get("flight_recorder_sha")
        elif kind == events.GRADE and ev.get("trial_id") == trial_id:
            grades.append(
                {
                    "task_sha": ev.get("task_sha"),
                    "assertions": ev.get("assertions"),
                    "binary_score": ev.get("binary_score"),
                    "fractional_score": ev.get("fractional_score"),
                    "grader": ev.get("grader"),
                    "override_of": ev.get("override_of"),
                    "ts": (ev.get("provenance") or {}).get("ts"),
                }
            )
        elif kind == events.CANT_GRADE and ev.get("trial_id") == trial_id:
            cant_grades.append(
                {
                    "reason": ev.get("reason"),
                    "override_of": ev.get("override_of"),
                    "ts": (ev.get("provenance") or {}).get("ts"),
                }
            )
        elif kind == events.FORENSIC_QUARANTINE:
            fq = ev.get("forensic_quarantine") or {}
            if fq.get("trial_id") == trial_id:
                quarantine = {"reason": fq.get("reason")}

    if record is None:
        return None

    cmp_id = comparison_id_for(record.get("task_id"), record.get("repetition", 0))
    for ev in evs:
        if ev.get("event") == events.JUDGE_VERDICT:
            v = ev.get("verdict") or {}
            if v.get("comparison_id") == cmp_id:
                verdicts.append(v)

    reports = [ev for ev in evs if ev.get("event") == events.FORENSICS_REPORT]
    if reports:
        fr = reports[-1].get("forensics_report") or {}
        metrics = (fr.get("metrics") or {}).get(trial_id)
        flags = [f for f in (fr.get("flags") or []) if f.get("trial_id") == trial_id]

    status, trajectory = resolve_trajectory(record.get("artifacts_path"), trajectory_sha)
    steps = (
        [s.model_dump(mode="json") for s in trajectory.steps]
        if trajectory is not None
        else None
    )
    fr_status, fr_record = resolve_flight_recorder(
        record.get("artifacts_path"), flight_recorder_sha
    )
    fr_entries = (
        [e.model_dump(mode="json") for e in fr_record.entries]
        if fr_record is not None
        else None
    )

    rec_flags = record.get("flags") or {}
    return {
        "trial_id": trial_id,
        "record": record,
        "comparison_id": cmp_id,
        "trajectory": {"status": status, "steps": steps},
        "flight_recorder": {"status": fr_status, "entries": fr_entries},
        "grade": {
            "grades": grades,
            "cant_grades": cant_grades,
            "binary_score": grades[-1]["binary_score"] if grades else None,
        },
        "verdicts": verdicts,
        "forensics": {"metrics": metrics, "flags": flags},
        "quarantine": quarantine,
        "egress": {
            "violation": rec_flags.get("egress_violation"),
            "attempts": rec_flags.get("egress_attempts"),
        },
    }


def _story_to_detail(story, trial_id):
    """Map a TrialStory to the trial_detail dict shape (what the migrated
    status/trial.py does), so trial_story is pinned independently of the
    consumer that adapts it."""
    if story is None:
        return None
    rec_flags = story.record.get("flags") or {}
    return {
        "trial_id": trial_id,
        "record": story.record,
        "comparison_id": story.comparison_id,
        "trajectory": {"status": story.trajectory_status, "steps": story.trajectory_steps},
        "flight_recorder": {
            "status": story.flight_recorder_status,
            "entries": story.flight_recorder_entries,
        },
        "grade": {
            "grades": story.grades,
            "cant_grades": story.cant_grades,
            "binary_score": story.grades[-1]["binary_score"] if story.grades else None,
        },
        "verdicts": story.verdicts,
        "forensics": {"metrics": story.forensics_metrics, "flags": story.forensics_flags},
        "quarantine": story.quarantine,
        "egress": {
            "violation": rec_flags.get("egress_violation"),
            "attempts": rec_flags.get("egress_attempts"),
        },
    }


# --- ledgers under test -------------------------------------------------------
@pytest.fixture
def rich(tmp_path):
    return rich_experiment(tmp_path)


def _ledger_ids(ledger) -> list[str]:
    return [ev["trial_record"]["trial_id"] for ev in find_events(ledger, events.TRIAL)]


_ALL_KINDS = sorted(events.REGISTERED_EVENTS)


# --- by_kind / latest / events -----------------------------------------------
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
@pytest.mark.parametrize("which", ["golden", "rich"])
def test_trials_view_matches_events(which, rich):
    ledger = _GOLDEN if which == "golden" else rich["ledger"]
    view = LedgerView(ledger)
    trials = view.trials()
    raw = find_events(ledger, events.TRIAL)
    assert len(trials) == len(raw)
    for tv, ev in zip(trials, raw):
        assert isinstance(tv, TrialEventView)
        assert tv.record == ev["trial_record"]
        assert tv.trial_id == ev["trial_record"]["trial_id"]
        # the reader rule: shas come from the EVENT, never the record
        assert tv.trajectory_sha == ev.get("trajectory_sha")
        assert tv.flight_recorder_sha == ev.get("flight_recorder_sha")


# --- grade / verdict / quarantine projections --------------------------------
@pytest.mark.parametrize("which", ["golden", "rich"])
def test_grade_projections_match_oracles(which, rich):
    ledger = _GOLDEN if which == "golden" else rich["ledger"]
    view = LedgerView(ledger)
    assert view.grades_by_trial() == _oracle_grades_by_trial(ledger)
    assert view.latest_grade_by_trial() == _oracle_latest_grade_by_trial(ledger)


@pytest.mark.parametrize("which", ["golden", "rich"])
def test_verdict_and_quarantine_projections_match_oracles(which, rich):
    ledger = _GOLDEN if which == "golden" else rich["ledger"]
    view = LedgerView(ledger)
    assert view.verdicts_by_comparison() == _oracle_verdicts_by_comparison(ledger)
    assert view.quarantined_trial_ids() == _oracle_quarantined_trial_ids(ledger)


def test_rich_projections_are_non_trivial(rich):
    """Guard the oracles are actually exercising the interesting states."""
    view = LedgerView(rich["ledger"])
    assert view.quarantined_trial_ids()  # the fixture quarantines t2/treatment
    assert view.verdicts_by_comparison()  # one advisory verdict on cmp-t1-r0
    assert view.latest(events.FORENSICS_REPORT) is not None


def test_provenance_ts_helper():
    view = LedgerView(_GOLDEN)
    ev = view.by_kind(events.TRIAL)[0]
    assert view.provenance_ts(ev) == ev["provenance"]["ts"]
    assert view.provenance_ts({}) is None
    assert view.provenance_ts({"provenance": {}}) is None


# --- trial_story ≡ the hand-rolled join --------------------------------------
@pytest.mark.parametrize("which", ["golden", "rich"])
def test_trial_story_matches_hand_rolled_join(which, rich):
    ledger = _GOLDEN if which == "golden" else rich["ledger"]
    view = LedgerView(ledger)
    for trial_id in _ledger_ids(ledger):
        story = view.trial_story(trial_id)
        assert _story_to_detail(story, trial_id) == _oracle_trial_detail(ledger, trial_id), trial_id


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
