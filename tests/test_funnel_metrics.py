"""Funnel-metric properties [verdi-go integration plan §6, exploratory tier].

Pins the three per-trial funnel metrics (``grounded_before_edit``,
``checked_after_last_edit``, ``verdict_heeded``) computed by
``scripts/funnel_metrics.py`` from a trial's ``groundwork-mcp.jsonl`` (real
``groundwork mcp --log`` shape) × its v3 trajectory. Covers every metric's
true / false / null case, the telemetry-null discipline (an absent log is
``null``, never ``false``), the honest-ordering limitation (precedence is read
off the transcript's own line order, gated on trajectory edit-presence),
``verdict_heeded``'s surfaced-but-shipped operationalization, byte-determinism,
and the experiment-dir ledger walk. Hermetic — no binaries, no Docker.

The tool is a standalone script (imports no harness code); this loads it by path,
the ``test_corpus_groundwork_v0`` precedent, and reads the committed synthetic
fixtures the shakedown script also plants (``tests/fixtures/funnel/``).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_FUNNEL_PY = _REPO / "scripts" / "funnel_metrics.py"
_FIXTURES = _REPO / "tests" / "fixtures" / "funnel"


def _load_funnel():
    spec = importlib.util.spec_from_file_location("funnel_metrics", _FUNNEL_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


fm = _load_funnel()


def _fixture(name: str) -> tuple[str, dict]:
    return fm.read_trial_artifacts(_FIXTURES / name)


# --------------------------------------------------------------------------- #
# per-metric true / false / null
# --------------------------------------------------------------------------- #
def test_grounded_before_edit_true_when_first_call_is_ground_and_edits_exist():
    mcp, traj = _fixture("grounded_checked")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert m["grounded_before_edit"] is True
    assert m["n_file_edits"] == 1


def test_grounded_before_edit_false_when_first_call_is_not_ground():
    mcp, traj = _fixture("late_ground")  # first logged call is `reach`, not `ground`
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert m["grounded_before_edit"] is False


def test_precedence_metrics_null_when_no_file_edit():
    """No edit in the trajectory ⇒ 'before/after the edit' is not applicable ⇒ null
    (NOT false): the applicability gate, honest about an un-assessable trial."""
    mcp, traj = _fixture("no_edits")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert m["grounded_before_edit"] is None
    assert m["checked_after_last_edit"] is None
    # the log itself was present and had calls — the null is edit-absence, not log-absence
    assert m["has_mcp_log"] is True and m["n_mcp_calls"] == 2


def test_checked_after_last_edit_true_when_last_call_is_a_check():
    mcp, traj = _fixture("grounded_checked")  # last logged call is `fitness`
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert m["checked_after_last_edit"] is True


def test_checked_after_last_edit_false_when_last_call_is_not_a_check():
    mcp, traj = _fixture("late_ground")  # last logged call is `reach`
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert m["checked_after_last_edit"] is False


def test_verdict_heeded_true_when_surfaced_and_not_shipped():
    mcp, traj = _fixture("grounded_checked")  # non-error fitness == a surfaced verdict
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert m["verdict_surfaced"] is True
    assert m["verdict_heeded"] is True


def test_verdict_heeded_false_when_surfaced_but_shipped():
    """The headline 'surfaced-but-shipped' case: the tool produced a verdict (a
    non-error fitness/ground call in the log) yet the trial shipped a violation the
    merge-time gate BLOCKed → the verdict was NOT heeded [plan §6 / Tier 9b]."""
    mcp, traj = _fixture("grounded_checked")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=True)
    assert m["verdict_heeded"] is False


def test_verdict_heeded_null_when_no_verdict_surfaced():
    """Only errored fitness calls ⇒ no verdict was ever produced for the agent ⇒
    there is nothing to heed ⇒ null, even though the ship outcome is known."""
    mcp, traj = _fixture("error_check")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=True)
    assert m["verdict_surfaced"] is False
    assert m["verdict_heeded"] is None
    # an errored `fitness` is still the last call by NAME (checked_after gates on name),
    # but it is NOT a surfaced verdict (isError) — the two signals are kept distinct.
    assert m["checked_after_last_edit"] is True
    assert m["grounded_before_edit"] is False


def test_verdict_heeded_null_when_ship_outcome_unknown():
    mcp, traj = _fixture("grounded_checked")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=None)
    assert m["verdict_heeded"] is None


# --------------------------------------------------------------------------- #
# telemetry-null discipline: an absent MCP log is null, never false
# --------------------------------------------------------------------------- #
def test_absent_mcp_log_is_null_never_false():
    """A control/bare arm never had the surface: every metric is null (not
    applicable), NOT false — the whole point of the exercise (telemetry_null)."""
    _, traj = _fixture("grounded_checked")  # a real trajectory, but NO mcp log
    m = fm.compute_trial_metrics(None, traj, shipped_violation=True)
    assert m["grounded_before_edit"] is None
    assert m["checked_after_last_edit"] is None
    assert m["verdict_heeded"] is None
    assert m["has_mcp_log"] is False
    # false must never appear for a not-applicable trial
    assert not any(m[k] is False for k in fm.METRIC_IDS)


def test_read_trial_artifacts_absent_log_returns_none():
    """A trial dir with a trajectory but no groundwork-mcp.jsonl reads mcp as None
    (honest absence), so the control arm computes as null end to end."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "trajectory.json").write_text((_FIXTURES / "grounded_checked" / "trajectory.json").read_text())
        mcp, traj = fm.read_trial_artifacts(d)
        assert mcp is None and isinstance(traj, dict)


# --------------------------------------------------------------------------- #
# parsing the real --log shape
# --------------------------------------------------------------------------- #
def test_parse_mcp_log_skips_init_and_malformed_preserves_order():
    text = (
        '{"init":true,"session":"1"}\n'
        '{"call":{"name":"ground","arguments":{"fqn":"F"}},"service":"graph.json","session":"1"}\n'
        "not json at all\n"
        "42\n"  # a bare scalar — not an object, skipped
        '{"call":{"name":"fitness","arguments":{}},"service":"graph.json","session":"1","isError":true}\n'
    )
    calls = fm.parse_mcp_log(text)
    assert [c.name for c in calls] == ["ground", "fitness"]  # init + junk skipped, order kept
    assert calls[0].is_error is False
    assert calls[1].is_error is True


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_determinism_same_bytes_twice():
    mcp, traj = _fixture("grounded_checked")
    a = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    b = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert a == b
    rows = [{"trial_id": "t", "arm": "grounded", "task_id": "gw-r2", "shipped_violation": False, **a}]
    assert fm.render_json(rows) == fm.render_json(rows)
    assert fm.rows_to_csv(rows) == fm.rows_to_csv(rows)


def test_csv_renders_null_explicitly_not_blank():
    rows = [{"trial_id": "t", "arm": "bare", "task_id": "gw-r2", "shipped_violation": None,
             **fm.compute_trial_metrics(None, None, None)}]
    csv_text = fm.rows_to_csv(rows)
    body = csv_text.splitlines()[1]
    # every not-applicable cell is the explicit token 'null', never an empty field
    assert ",null," in body or body.endswith(",null")
    assert ",," not in body  # no blank cell that could read as false


# --------------------------------------------------------------------------- #
# experiment-dir ledger walk
# --------------------------------------------------------------------------- #
def _write_ledger(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_experiment_walk_joins_ledger_grade_with_artifacts(tmp_path):
    """End-to-end experiment mode: a grounded trial (planted log, gate PASS →
    heeded), a grounded trial whose gate BLOCKed (surfaced-but-shipped → not
    heeded), and a bare control (no log → all null). Deterministic ordering."""
    exp = tmp_path / "expt"
    exp.mkdir()

    def _artifacts(name: str, fixture: str | None) -> Path:
        a = exp / "runs" / name / "artifacts"
        a.mkdir(parents=True)
        if fixture:
            shutil.copy(_FIXTURES / fixture / "groundwork-mcp.jsonl", a / "groundwork-mcp.jsonl")
            shutil.copy(_FIXTURES / fixture / "trajectory.json", a / "trajectory.json")
        else:
            # control: a trajectory but NO mcp log (the surface was never wired)
            shutil.copy(_FIXTURES / "grounded_checked" / "trajectory.json", a / "trajectory.json")
        return a

    a_heeded = _artifacts("t-heeded", "grounded_checked")
    a_shipped = _artifacts("t-shipped", "grounded_checked")
    a_control = _artifacts("t-control", None)

    def _trial(tid, arm, art):
        return {"event": "trial", "trial_record": {
            "trial_id": tid, "arm": arm, "task_id": "gw-r2", "artifacts_path": str(art)}}

    def _grade(tid, verdict, binary):
        return {"event": "grade", "trial_id": tid, "binary_score": binary,
                "assertions": [{"source": "plugin:groundwork", "id": "groundwork:verdict",
                                "result": verdict}]}

    _write_ledger(exp / "ledger.ndjson", [
        _trial("t-heeded", "grounded", a_heeded), _grade("t-heeded", "passed", True),
        _trial("t-shipped", "grounded", a_shipped), _grade("t-shipped", "failed", False),
        _trial("t-control", "bare", a_control), _grade("t-control", "failed", False),
    ])

    rows = {r["trial_id"]: r for r in fm.iter_experiment_trials(exp)}
    assert rows["t-heeded"]["verdict_heeded"] is True
    assert rows["t-heeded"]["shipped_violation"] is False
    assert rows["t-shipped"]["verdict_heeded"] is False       # surfaced-but-shipped
    assert rows["t-shipped"]["shipped_violation"] is True
    # control: no log ⇒ every metric null, and shipped is still derivable from its grade
    assert rows["t-control"]["has_mcp_log"] is False
    assert all(rows["t-control"][k] is None for k in fm.METRIC_IDS)

    # deterministic order + aggregate shape
    ordered = [r["trial_id"] for r in fm.iter_experiment_trials(exp)]
    assert ordered == sorted(ordered, key=lambda t: t)  # sorted by (task, arm, trial)
    agg = fm.aggregate(list(rows.values()))
    assert agg["verdict_heeded"] == {"true": 1, "false": 1, "null": 1, "rate": 0.5}
    assert agg["n_with_mcp_log"] == 2


def test_shipped_violation_falls_back_to_binary_score_without_plugin(tmp_path):
    """When the groundwork plugin verdict is absent, the composite command-holdout
    binary score stands in: a functionally-correct exemplar fails ONLY on the gate,
    so binary_score False ⇒ shipped a violation."""
    grade = {"assertions": [{"source": "holdout_test", "id": "h1", "result": "failed"}],
             "binary_score": False}
    assert fm._shipped_violation_from_grade(grade) is True
    grade_pass = {"assertions": [], "binary_score": True}
    assert fm._shipped_violation_from_grade(grade_pass) is False
    assert fm._shipped_violation_from_grade(None) is None


# --------------------------------------------------------------------------- #
# log v2: per-rule verdict_heeded (fixtures shaped on the REAL producer emission —
# `groundwork mcp --log` over a layeredsvc branch that trips a `layering` violation)
# --------------------------------------------------------------------------- #
_V2_LAYERING_ID = (
    "layering|(*example.com/layeredsvc/internal/handler.Server).GetUserFast"
    "|(*example.com/layeredsvc/internal/store.Store).SelectUser"
)


def test_v2_marker_detected_and_violations_surfaced():
    mcp, traj = _fixture("v2_surfaced")
    assert fm.mcp_log_version(mcp) == 2  # the "log":2 init marker
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=None)
    assert m["log_version"] == 2
    assert m["verdict_violations_surfaced"] is True
    assert m["verdict_heeded"] is None  # ship outcome unknown ⇒ nothing to heed yet


def test_v2_verdict_heeded_false_when_surfaced_rule_shipped():
    """The precise per-rule case: the tool surfaced a `layering` violation and the
    gate BLOCKed `layering` — the agent shipped a rule it was shown → NOT heeded,
    and the overlap NAMES the surfaced identity that shipped."""
    mcp, traj = _fixture("v2_surfaced")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=True, shipped_rules=["layering"])
    assert m["verdict_heeded"] is False
    assert m["verdict_heeded_overlap"] == [_V2_LAYERING_ID]


def test_v2_verdict_heeded_true_when_shipped_rule_was_not_surfaced():
    """Per-rule honesty the coarse form cannot manage: the gate BLOCKed a rule kind
    the tool never surfaced (`must_not_reach`), so the tool's `layering` warning was
    not ignored → heeded, with an empty (computed, not null) overlap."""
    mcp, traj = _fixture("v2_surfaced")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=True, shipped_rules=["must_not_reach"])
    assert m["verdict_heeded"] is True
    assert m["verdict_heeded_overlap"] == []


def test_v2_verdict_heeded_true_when_gate_passed():
    mcp, traj = _fixture("v2_surfaced")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False, shipped_rules=[])
    assert m["verdict_heeded"] is True
    assert m["verdict_heeded_overlap"] is None  # nothing shipped ⇒ un-nameable, not []


def test_v2_verdict_heeded_coarse_fallback_without_rule_kinds():
    """A v2 log but no named BLOCKed kinds (a binary_score grade) ⇒ degrade to the
    coarse 'surfaced-and-shipped' → not heeded, overlap un-nameable (None)."""
    mcp, traj = _fixture("v2_surfaced")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=True, shipped_rules=None)
    assert m["verdict_heeded"] is False
    assert m["verdict_heeded_overlap"] is None


def test_v2_verdict_heeded_null_when_no_violation_surfaced():
    """A v2 `fitness` call that surfaced NO violation (violated empty) ⇒ nothing to
    heed, even though the gate blocked — the sharpest v2 gain over v1's coarse
    'a card was produced' surfacing."""
    mcp, traj = _fixture("v2_clean")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=True, shipped_rules=["layering"])
    assert m["verdict_violations_surfaced"] is False
    assert m["verdict_heeded"] is None
    assert m["verdict_heeded_overlap"] is None


def test_v2_determinism_same_bytes_twice():
    mcp, traj = _fixture("v2_surfaced")
    a = fm.compute_trial_metrics(mcp, traj, shipped_violation=True, shipped_rules=["layering"])
    b = fm.compute_trial_metrics(mcp, traj, shipped_violation=True, shipped_rules=["layering"])
    assert a == b
    rows = [{"trial_id": "t", "arm": "grounded", "task_id": "gw-r2", "shipped_violation": True, **a}]
    assert fm.render_json(rows) == fm.render_json(rows)


def test_v2_experiment_walk_per_rule_heeded(tmp_path):
    """End-to-end v2 through the ledger walk: a v2 log surfacing `layering` + a grade
    whose per-rule groundwork assertion BLOCKed `layering` ⇒ verdict_heeded False and
    overlap naming the identity — via _shipped_rules_from_grade, no manual kinds."""
    exp = tmp_path / "expt"
    a = exp / "runs" / "t1" / "artifacts"
    a.mkdir(parents=True)
    shutil.copy(_FIXTURES / "v2_surfaced" / "groundwork-mcp.jsonl", a / "groundwork-mcp.jsonl")
    shutil.copy(_FIXTURES / "v2_surfaced" / "trajectory.json", a / "trajectory.json")
    _write_ledger(exp / "ledger.ndjson", [
        {"event": "trial", "trial_record": {
            "trial_id": "t1", "arm": "grounded", "task_id": "gw-r2", "artifacts_path": str(a)}},
        {"event": "grade", "trial_id": "t1", "binary_score": False, "assertions": [
            {"source": "plugin:groundwork", "id": "groundwork:verdict", "result": "failed"},
            {"source": "plugin:groundwork", "id": "layering", "result": "failed"},
        ]},
    ])
    rows = {r["trial_id"]: r for r in fm.iter_experiment_trials(exp)}
    assert rows["t1"]["verdict_heeded"] is False
    assert rows["t1"]["verdict_heeded_overlap"] == [_V2_LAYERING_ID]
    assert rows["t1"]["shipped_violation"] is True


def test_shipped_rules_from_grade_kinds_and_fallback():
    """_shipped_rules_from_grade names the BLOCKed rule kinds from per-rule groundwork
    assertions, excludes the top-line verdict, and returns None (⇒ coarse) when the
    plugin never ran."""
    grade = {"assertions": [
        {"source": "plugin:groundwork", "id": "groundwork:verdict", "result": "failed"},
        {"source": "plugin:groundwork", "id": "layering", "result": "failed"},
        {"source": "plugin:groundwork", "id": "must_not_reach", "result": "abstain"},  # a caution, not shipped
    ]}
    assert fm._shipped_rules_from_grade(grade) == ["layering"]
    # no groundwork assertions (binary_score fallback path) ⇒ None, not []
    assert fm._shipped_rules_from_grade({"assertions": [{"source": "holdout", "id": "h", "result": "failed"}]}) is None
    assert fm._shipped_rules_from_grade(None) is None


# --------------------------------------------------------------------------- #
# v1 unchanged: byte-for-byte regression pin
# --------------------------------------------------------------------------- #
def test_v1_log_output_unchanged_no_v2_keys():
    """Regression pin: a v1 log (no "log":2 marker) yields the byte-identical pre-v2
    dict — the same seven keys, no v2-only keys — so existing v1 fixtures and any
    committed v1 artifact render exactly as before the v2 upgrade."""
    mcp, traj = _fixture("grounded_checked")
    assert fm.mcp_log_version(mcp) == 0  # v1: no marker
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=False)
    assert m == {
        "grounded_before_edit": True,
        "checked_after_last_edit": True,
        "verdict_heeded": True,
        "has_mcp_log": True,
        "n_mcp_calls": 3,
        "n_file_edits": 1,
        "verdict_surfaced": True,
    }
    for k in ("log_version", "verdict_violations_surfaced", "verdict_heeded_overlap"):
        assert k not in m


def test_v1_shipped_rules_ignored_on_v1_log():
    """The MARKER, not the argument, selects the reading: a v1 log stays coarse even
    if per-rule kinds are supplied, and grows no v2 keys."""
    mcp, traj = _fixture("grounded_checked")
    m = fm.compute_trial_metrics(mcp, traj, shipped_violation=True, shipped_rules=["layering"])
    assert m["verdict_heeded"] is False       # coarse: surfaced and shipped
    assert "verdict_heeded_overlap" not in m  # no v2 keys leaked onto a v1 row
