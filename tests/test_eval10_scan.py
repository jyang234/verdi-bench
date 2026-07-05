"""Deterministic trial-artifact overlap scan [EVAL-10 AC-4, review fixes]."""

from __future__ import annotations

import pytest

from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
from harness.contamination.scan import TaskReferences, read_solution, scan_trials
from harness.ledger.events import record_trial
from tests.fixtures.builders import fixed_ctx

_ORACLE = """
def parse_config(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if "version" not in data:
        raise ValueError("config file is missing the required version key")
    entries = data.get("entries", [])
    return Config(version=data["version"], entries=entries)
"""

_HOLDOUT = """
def test_parse_config_rejects_missing_version(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"entries": [1, 2, 3]}))
    with pytest.raises(ValueError, match="version"):
        parse_config(cfg)
"""

_INDEPENDENT = """
class SettingsLoader:
    def load(self, filename):
        raw = Path(filename).read_text()
        parsed = yaml.safe_load(raw) or {}
        self._require_schema_field(parsed)
        return Settings.from_mapping(parsed)
"""


def _seed_trial(ledger, ctx, *, trial_id, task_id, arm, artifacts_path):
    rec = TrialRecord.assemble(
        trial_id=trial_id, task_id=task_id, arm=arm, repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(cost=1.0, wall_time_s=1.0),
        provenance=Provenance(image_digest="d"), flags=Flags(),
        artifacts_path=artifacts_path,
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))


def _workspace(tmp_path, name, *, solution, logs="transcript noise"):
    """A trial workspace in the engine's shape: solution files at the root,
    logs under artifacts/."""
    ws = tmp_path / name
    artifacts = ws / "artifacts"
    artifacts.mkdir(parents=True)
    (ws / "solution.py").write_text(solution, encoding="utf-8")
    (artifacts / "transcript.txt").write_text(logs, encoding="utf-8")
    return str(artifacts)


def test_h3_tampered_workspace_is_unscanned_never_clean(tmp_path):
    """F-H3: a workspace edited after grading committed its hash must be
    UNSCANNED — a post-grade scrub of leaked oracle bytes previously scanned
    'clean' and laundered that verdict onto the chain-anchored probe."""
    from pathlib import Path

    from harness.ledger.events import record_grade
    from harness.run.workspace import workspace_sha256

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    refs = {"task0": TaskReferences(oracle=_ORACLE)}
    leaked = _workspace(tmp_path, "ws-leak", solution=_ORACLE)
    _seed_trial(ledger, ctx, trial_id="c-1", task_id="task0", arm="control",
                artifacts_path=leaked)
    ws = Path(leaked).parent
    record_grade(
        ledger, ctx, trial_id="c-1", task_sha="s",
        assertions=[{"id": "h1", "source": "holdout_test", "result": "pass"}],
        binary_score=True,
        workspace_sha256=workspace_sha256(ws, artifacts_dir=leaked),
        workspace_walk_version=1,
    )
    (ws / "solution.py").write_text(_INDEPENDENT, encoding="utf-8")  # the scrub
    report = scan_trials(ledger, refs)
    assert report.overlap_flags == {}  # not scanned-clean
    (skip,) = report.skipped
    assert "sha_mismatch" in skip and "c-1" in skip

    # untampered control: the same commitment verifies and the leak flags
    ledger2 = tmp_path / "l2.ndjson"
    leaked2 = _workspace(tmp_path, "ws-leak2", solution=_ORACLE)
    _seed_trial(ledger2, ctx, trial_id="c-2", task_id="task0", arm="control",
                artifacts_path=leaked2)
    record_grade(
        ledger2, ctx, trial_id="c-2", task_sha="s",
        assertions=[{"id": "h1", "source": "holdout_test", "result": "pass"}],
        binary_score=True,
        workspace_sha256=workspace_sha256(Path(leaked2).parent, artifacts_dir=leaked2),
        workspace_walk_version=1,
    )
    report2 = scan_trials(ledger2, refs)
    assert report2.overlap_flags == {"control": {"task0": True}}
    assert report2.skipped == []


def test_scan_flags_workspace_solution_not_logs(tmp_path):
    """The scan reads the workspace solution (the judge's solution definition)
    and ignores the artifacts/ log tree: a verbatim oracle in the solution
    flags; the same content appearing only in a log does not [review fix]."""
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    refs = {"task0": TaskReferences(oracle=_ORACLE)}

    copied = _workspace(tmp_path, "ws-copied", solution=_ORACLE)
    _seed_trial(ledger, ctx, trial_id="c-1", task_id="task0", arm="control",
                artifacts_path=copied)
    # treatment wrote an independent solution but its LOG echoes the oracle —
    # instrument-captured telemetry must not flag the trial
    log_echo = _workspace(tmp_path, "ws-log", solution=_INDEPENDENT, logs=_ORACLE)
    _seed_trial(ledger, ctx, trial_id="t-1", task_id="task0", arm="treatment",
                artifacts_path=log_echo)

    report = scan_trials(ledger, refs)
    assert report.overlap_flags == {
        "control": {"task0": True},
        "treatment": {"task0": False},
    }
    assert report.alarms == [] and report.skipped == []


def test_scan_holdout_leak_is_alarmed_and_flagged(tmp_path):
    """A holdout reproduced in the solution raises the EVAL-4 insulation alarm,
    preserved as evidence (flag + alarm), and the sweep continues [AC-4]."""
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    refs = {"task0": TaskReferences(holdouts=(_HOLDOUT,))}
    leaked = _workspace(tmp_path, "ws-leak", solution="# tests\n" + _HOLDOUT)
    _seed_trial(ledger, ctx, trial_id="c-1", task_id="task0", arm="control",
                artifacts_path=leaked)
    clean = _workspace(tmp_path, "ws-clean", solution=_INDEPENDENT)
    _seed_trial(ledger, ctx, trial_id="t-1", task_id="task0", arm="treatment",
                artifacts_path=clean)

    report = scan_trials(ledger, refs)
    assert report.overlap_flags["control"]["task0"] is True
    assert report.overlap_flags["treatment"]["task0"] is False
    assert len(report.alarms) == 1
    assert "c-1" in report.alarms[0] and "holdout" in report.alarms[0]


def test_scan_missing_artifacts_is_disclosed_not_cwd(tmp_path, monkeypatch):
    """An absent/empty artifacts_path is a disclosed UNSCANNED trial — never a
    scan of the current working directory [review fix: Path('') is cwd]."""
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    # run from a directory that CONTAINS the oracle: a cwd-slurp would flag
    trap = tmp_path / "trap"
    trap.mkdir()
    (trap / "oracle_copy.py").write_text(_ORACLE, encoding="utf-8")
    monkeypatch.chdir(trap)

    refs = {"task0": TaskReferences(oracle=_ORACLE)}
    _seed_trial(ledger, ctx, trial_id="c-1", task_id="task0", arm="control",
                artifacts_path="")  # never recorded
    _seed_trial(ledger, ctx, trial_id="c-2", task_id="task0", arm="control",
                artifacts_path=str(tmp_path / "gone" / "artifacts"))  # vanished

    report = scan_trials(ledger, refs)
    assert report.overlap_flags == {}  # nothing scanned, nothing flagged
    assert len(report.skipped) == 2
    assert all("UNSCANNED" in s for s in report.skipped)

    assert read_solution("") is None
    assert read_solution(None) is None


def test_scan_unmeasurable_tasks_contribute_nothing(tmp_path):
    """A task with no oracle and no holdouts is unmeasurable by this channel:
    no flag entry, no skip — that is a property of the corpus, not a failure."""
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    ws = _workspace(tmp_path, "ws", solution=_INDEPENDENT)
    _seed_trial(ledger, ctx, trial_id="c-1", task_id="task0", arm="control",
                artifacts_path=ws)
    report = scan_trials(ledger, {"task0": TaskReferences()})
    assert report.overlap_flags == {} and report.skipped == []


def test_scan_or_merges_repetitions(tmp_path):
    """Multiple trials of one (arm, task) OR-merge: one leaking repetition
    flags the pair even when a later repetition is clean."""
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    refs = {"task0": TaskReferences(oracle=_ORACLE)}
    _seed_trial(ledger, ctx, trial_id="c-1", task_id="task0", arm="control",
                artifacts_path=_workspace(tmp_path, "ws1", solution=_ORACLE))
    _seed_trial(ledger, ctx, trial_id="c-2", task_id="task0", arm="control",
                artifacts_path=_workspace(tmp_path, "ws2", solution=_INDEPENDENT))
    report = scan_trials(ledger, refs)
    assert report.overlap_flags == {"control": {"task0": True}}


def test_m_c3_quarantined_trial_is_skipped_disclosed(tmp_path):
    """F-M-C3 resolution path: a forensically-quarantined trial (a ledgered
    human disposition) is excluded from the scan — disclosed, never silent —
    so quarantining an intentional/false-positive holdout leak and re-running
    scan+probe legitimately clears the insulation fence."""
    from harness.ledger.events import record_forensic_quarantine

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    refs = {"task0": TaskReferences(holdouts=(_HOLDOUT,))}
    leaky = _workspace(tmp_path, "ws-leak", solution=_HOLDOUT)
    _seed_trial(ledger, ctx, trial_id="c-1", task_id="task0", arm="control",
                artifacts_path=leaky)

    before = scan_trials(ledger, refs)
    assert before.alarms  # the leak alarms pre-quarantine

    record_forensic_quarantine(ledger, ctx, trial_id="c-1",
                               reason="intentional fixture leak")
    after = scan_trials(ledger, refs)
    assert after.alarms == []
    (skip,) = after.skipped
    assert "quarantined" in skip and "c-1" in skip
