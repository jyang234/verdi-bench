"""EVAL-13 — live observability: heartbeat, tail cursor, status, observer.

AC map: heartbeat sidecar (AC-1), ledger tail cursor (AC-2), status aggregate
(AC-3) + verb (AC-4), read-only HTTP observer (AC-5) + self-contained
unblinded-disclosure page (AC-6), structural read-only/LLM-free contracts
(AC-7). Spec: docs/design/specs/eval13.spec.md.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
from harness.ledger import events as ledger_events
from harness.ledger.chain import append_event
from harness.ledger.query import TailOffsetError, find_events, read_events, tail_events
from harness.plan.interleave import derive_schedule, enumerate_trials
from harness.run.engines.fake import FakeEngine
from harness.run.heartbeat import HEARTBEAT_FILENAME, read_heartbeat
from harness.run.interleave import schedule
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from harness.status.aggregate import compute_status
from tests.fixtures.builders import ctx_for, locked_experiment, seed_trial_and_grade
from tests.fixtures.servers import serve_experiment
from tests.fixtures.tamper import reencode_line

_REPO = Path(__file__).resolve().parents[1]


def _arms_tasks(cost: float = 0.01):
    arms = {
        "A": Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022"),
        "B": Arm(name="B", platform="codex", model="openai/gpt-4o-2024-08-06"),
    }
    tasks = {
        tid: Task(id=tid, prompt="p", fake_behavior={"native_log": {"total_cost_usd": cost}})
        for tid in ["t1", "t2"]
    }
    return arms, tasks


def _run(tmp_path, order, *, arms, tasks, engine=None, cost_ceiling=100.0):
    hb_path = tmp_path / HEARTBEAT_FILENAME
    result = schedule(
        order,
        tasks=tasks,
        arms=arms,
        workspace_root=tmp_path / "ws",
        ledger_path=tmp_path / "l.ndjson",
        ctx=ctx_for(tmp_path),
        config=RunConfig(engine=engine or FakeEngine()),
        cost_ceiling=cost_ceiling,
        heartbeat_path=hb_path,
    )
    return result, hb_path, tmp_path / "l.ndjson"


# --- AC-1: heartbeat sidecar ---------------------------------------------------
def test_ac1_heartbeat_lifecycle_and_clock_seam(tmp_path):
    arms, tasks = _arms_tasks(cost=0.25)
    order = derive_schedule(7, enumerate_trials(["t1", "t2"], ["A", "B"], 1))
    _, hb_path, ledger = _run(tmp_path, order, arms=arms, tasks=tasks)

    doc = read_heartbeat(hb_path)
    assert doc["schema_version"] == 1
    assert doc["experiment_id"] == tmp_path.name  # the dir-derived id ctx_for stamps
    assert doc["state"] == "finished"
    assert doc["in_flight"] is None
    # counters agree with the ledger's own trial events [AC-1]
    assert doc["cells"] == {"planned": 4, "done": len(find_events(ledger, "trial")), "infra_failures": 0}
    assert doc["cells"]["done"] == 4
    # only the claude_code arm reports cost (codex's is null by design — D004
    # asymmetry, never imputed): 2 of the 4 trials contribute 0.25 each
    assert abs(doc["spend"]["accumulated"] - 2 * 0.25) < 1e-9
    assert doc["spend"]["ceiling"] == 100.0
    # timestamps came from the injected EventContext clock, not the wall clock
    assert doc["ts"].startswith("2026-01-01T00:00:")
    # telemetry, not evidence: no heartbeat event kind reached the chain
    assert {e["event"] for e in read_events(ledger)} == {"trial", "executed_order"}


class _PeekEngine:
    """Reads the sidecar mid-run — a deterministic in-trial observation point
    (no threads, no sleeps): whatever the file says *while an engine runs* is
    exactly what a live observer would see."""

    name = "fake"

    def __init__(self, hb_path: Path):
        self._inner = FakeEngine()
        self._hb_path = hb_path
        self.seen: list[dict] = []

    def run(self, request):
        self.seen.append(read_heartbeat(self._hb_path))
        return self._inner.run(request)


def test_ac1_heartbeat_in_flight_visible_mid_trial(tmp_path):
    arms, tasks = _arms_tasks()
    order = derive_schedule(3, enumerate_trials(["t1"], ["A", "B"], 1))
    engine = _PeekEngine(tmp_path / HEARTBEAT_FILENAME)
    _run(tmp_path, order, arms=arms, tasks=tasks, engine=engine)

    assert len(engine.seen) == len(order)
    for doc, planned in zip(engine.seen, order):
        assert doc["state"] == "running"
        flight = doc["in_flight"]
        assert (flight["task_id"], flight["arm"], flight["repetition"]) == (
            planned.task_id, planned.arm, planned.repetition,
        )
        assert flight["attempt"] == 1
        assert flight["trial_id"].startswith("trial-")
        assert flight["started_ts"].startswith("2026-01-01T")


def test_ac1_heartbeat_ceiling_stop_state(tmp_path):
    arms, tasks = _arms_tasks(cost=0.40)
    order = derive_schedule(9, enumerate_trials(["t1", "t2"], ["A", "B"], 2))
    result, hb_path, ledger = _run(tmp_path, order, arms=arms, tasks=tasks, cost_ceiling=1.00)

    assert result.stopped_cost_ceiling is True
    doc = read_heartbeat(hb_path)
    assert doc["state"] == "stopped_cost_ceiling"
    assert doc["in_flight"] is None
    assert doc["spend"]["accumulated"] >= 1.00
    ran = len(find_events(ledger, "trial"))
    assert doc["cells"]["done"] == ran and ran < len(order)


# --- AC-2: ledger tail cursor ----------------------------------------------------
def test_ac2_tail_cursor_no_loss_no_duplication(tmp_path):
    path = tmp_path / "l.ndjson"
    assert tail_events(path) == ([], 0)  # absent: nothing recorded yet, not an error

    for i in range(3):
        append_event(path, {"event": "e", "i": i})
    first, off1 = tail_events(path, 0)
    assert [e["i"] for e in first] == [0, 1, 2]

    again, off_same = tail_events(path, off1)
    assert again == [] and off_same == off1  # cursor at head: no dup, no drift

    for i in range(3, 5):
        append_event(path, {"event": "e", "i": i})
    second, off2 = tail_events(path, off1)
    assert [e["i"] for e in second] == [3, 4]  # exactly the appended events, once
    assert off2 == path.stat().st_size

    # beyond-EOF and negative cursors are rewrite evidence / caller bugs — loud
    with pytest.raises(TailOffsetError):
        tail_events(path, off2 + 1)
    with pytest.raises(TailOffsetError):
        tail_events(path, -1)
    with pytest.raises(TailOffsetError):
        tail_events(tmp_path / "gone.ndjson", 10)  # absent file + nonzero cursor


def test_ac2_tail_leaves_partial_line_unconsumed(tmp_path):
    path = tmp_path / "l.ndjson"
    append_event(path, {"event": "e", "i": 0})
    _, off = tail_events(path, 0)

    # a torn tail (foreign writer / mid-crash artifact): no newline yet
    with open(path, "ab") as fh:
        fh.write(b'{"event":"partial","i":1')
    got, off2 = tail_events(path, off)
    assert got == [] and off2 == off  # not consumed; cursor does not advance

    with open(path, "ab") as fh:
        fh.write(b"}\n")
    got, off3 = tail_events(path, off2)
    assert [e["i"] for e in got] == [1]
    assert off3 == path.stat().st_size


# --- AC-3: status aggregate -------------------------------------------------------
def _fixture_experiment(tmp_path) -> Path:
    """A scripted lifecycle: lock, tasks, 3 trials (2 graded, 1 pending on a
    transient cant_grade), 2 judge verdicts (1 CANT_JUDGE), a review packet +
    human verdict, and a quarantine."""
    _, _, ledger = locked_experiment(tmp_path)
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}, {"id": "t2", "prompt": "p"}]}),
        encoding="utf-8",
    )
    ctx = ctx_for(tmp_path)
    seed_trial_and_grade(
        ledger, ctx, trial_id="tr1", task_id="t1", arm="control", telemetry={"cost": 1.5}
    )
    seed_trial_and_grade(
        ledger, ctx, trial_id="tr2", task_id="t1", arm="treatment",
        telemetry={"cost": 1.0}, passed=False,
    )
    rec = TrialRecord.assemble(
        trial_id="tr3", task_id="t2", arm="control", repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(), provenance=Provenance(),
        flags=Flags(egress_violation=False), artifacts_path="/tmp/tr3/artifacts",
    )
    ledger_events.record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    ledger_events.record_cant_grade(
        ledger, ctx, trial_id="tr3", reason="grader_unavailable"  # transient ⇒ pending
    )
    ledger_events.append_verdict(
        ledger, ctx, verdict={"comparison_id": "c1", "winner": "CANT_JUDGE"}
    )
    ledger_events.append_verdict(ledger, ctx, verdict={"comparison_id": "c2", "winner": "A"})
    ledger_events.record_review_packet_built(
        ledger, ctx, comparison_id="c2", task_id="t1", task_class="code",
        response_map={"1": "control", "2": "treatment"}, seed=1,
    )
    ledger_events.append_human_verdict(
        ledger, ctx, verdict={"comparison_id": "c2", "winner": "1"}
    )
    ledger_events.record_forensic_quarantine(
        ledger, ctx, trial_id="tr2", reason="confirmed holdout tamper"
    )
    return ledger


def test_ac3_status_snapshot_from_fixture_ledger(tmp_path):
    _fixture_experiment(tmp_path)
    snap = compute_status(tmp_path)

    assert snap["schema_version"] == 1
    assert snap["experiment_id"] == tmp_path.name
    assert snap["chain"]["ok"] is True and snap["chain"]["events"] == 12
    assert snap["heartbeat"] is None  # no run executed — tolerated, not an error

    st = snap["stages"]
    assert st["lock"]["locked"] is True and st["lock"]["seed"] == 1234
    assert st["spec"]["arms"] == ["control", "treatment"]
    assert st["spec"]["repetitions"] == 3
    assert st["spec_error"] is None
    # 2 tasks × 2 arms × 3 reps, via run's own enumeration
    assert st["cells"] == {"planned": 12, "done": 3, "infra_failures": 0}
    assert st["per_arm"] == {
        "control": {"trials": 2, "completed": 2, "timeout": 0, "infra_failed": 0, "cost": 1.5},
        "treatment": {"trials": 1, "completed": 1, "timeout": 0, "infra_failed": 0, "cost": 1.0},
    }
    assert st["spend"]["accumulated"] == 2.5  # tr3's null cost never imputed
    assert st["spend"]["ceiling"] == 25.0 and st["spend"]["currency"] == "USD"
    assert st["spend"]["stopped_cost_ceiling"] is False
    # grade mirrors bench grade's skip vocabulary: transient cant_grade ⇒ pending
    assert st["grade"] == {"graded": 2, "cant_grade_terminal": 0, "pending": 1}
    # pairs_ready: only (t1, rep0) has both locked arms' trials; expected =
    # planned cells (12) / arms (2) [EVAL-14 additive denominators]
    assert st["judge"] == {
        "verdicts": 2, "cant_judge": 1, "pairs_ready": 1, "pairs_expected": 6,
    }
    assert st["last_event_ts"].startswith("2026-01-01T")
    assert st["review"] == {"packets": 1, "human_verdicts": 1, "reveals": 0}
    assert st["process_scores"] == 0
    assert st["forensics"] == {"reports": 0, "latest": None}
    assert st["quarantines"] == [
        {"trial_id": "tr2", "reason": "confirmed holdout tamper"}
    ]
    assert st["contamination_probes"] == 0
    assert st["analyze"]["selfcheck"] == "missing"
    assert st["analyze"]["renders"] == {"official": 0, "exploratory": 0}
    assert st["analyze"]["last_render"] is None


def test_ac3_status_is_pure_read(tmp_path):
    ledger = _fixture_experiment(tmp_path)
    before_bytes = ledger.read_bytes()
    before_listing = sorted(p.name for p in tmp_path.iterdir())

    compute_status(tmp_path)

    assert ledger.read_bytes() == before_bytes  # appended nothing
    assert sorted(p.name for p in tmp_path.iterdir()) == before_listing  # wrote nothing


def test_ac3_status_broken_chain_fails_closed(tmp_path):
    ledger = _fixture_experiment(tmp_path)
    # a live heartbeat exists (operational — must still be surfaced)
    (tmp_path / HEARTBEAT_FILENAME).write_text(
        json.dumps({"schema_version": 1, "state": "running"}), encoding="utf-8"
    )
    # tamper a mid-chain line: its successor's prev_hash no longer matches
    reencode_line(ledger, 1, lambda ev: ev.update(seed_of_doubt=True))

    snap = compute_status(tmp_path)
    assert snap["chain"]["ok"] is False
    assert "prev_hash" in snap["chain"]["detail"]
    assert snap["stages"] is None  # unverified ledger content is withheld
    assert snap["heartbeat"]["state"] == "running"  # operational: shown regardless


# --- AC-4: the status verb ---------------------------------------------------------
def test_ac4_status_verb_json_and_readonly(tmp_path):
    from harness.cli import app

    ledger = _fixture_experiment(tmp_path)
    before = ledger.read_bytes()
    runner = CliRunner()

    result = runner.invoke(app, ["status", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == compute_status(tmp_path)
    assert ledger.read_bytes() == before  # read-only: no event appended

    human = runner.invoke(app, ["status", str(tmp_path)])
    assert human.exit_code == 0, human.output
    assert "chain    OK" in human.output and "12" in human.output


# --- AC-5 / AC-6: the observer server + page ------------------------------------------
def _dir_digest(root: Path) -> list[tuple[str, str]]:
    return sorted(
        (str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*")
        if p.is_file()
    )


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_ac5_serve_endpoints_read_only(tmp_path):
    from harness.analyze.timeline import trial_timeline

    ledger = _fixture_experiment(tmp_path)
    before = _dir_digest(tmp_path)
    with serve_experiment(tmp_path) as base:
        assert _get_json(base + "/api/status") == compute_status(tmp_path)

        page1 = _get_json(base + "/api/events?offset=0")
        assert page1["next_offset"] == ledger.stat().st_size
        assert [e["event"] for e in page1["events"]] == [
            e["event"] for e in read_events(ledger)
        ]
        page2 = _get_json(base + f"/api/events?offset={page1['next_offset']}")
        assert page2 == {"events": [], "next_offset": page1["next_offset"]}

        assert _get_json(base + "/api/timeline") == json.loads(
            json.dumps(trial_timeline(ledger))
        )
    assert _dir_digest(tmp_path) == before  # serving mutated nothing


def test_ac5_serve_refuses_non_get_and_unknown_paths(tmp_path):
    _fixture_experiment(tmp_path)
    with serve_experiment(tmp_path) as base:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            req = urllib.request.Request(base + "/api/status", method=method, data=b"")
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(req)
            assert excinfo.value.code == 405
            assert excinfo.value.headers["Allow"] == "GET"

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/nope")
        assert excinfo.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/events?offset=abc")
        assert excinfo.value.code == 400

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/events?offset=999999")
        assert excinfo.value.code == 409  # shrunken-ledger cursor: rewrite evidence

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/events?offset=-1")
        assert excinfo.value.code == 400  # PRA-L10: negative offset is malformed (400)


def test_m10_data_routes_fail_closed_on_broken_chain(tmp_path):
    """PRA-M10: with a tampered ledger, every ledger-reading route withholds its
    content (409), not just /api/status — the trials list, timeline, drill-down,
    and compare no longer render tampered events."""
    ledger = _fixture_experiment(tmp_path)
    reencode_line(ledger, 1, lambda ev: ev.update(seed_of_doubt=True))

    with serve_experiment(tmp_path) as base:
        for route in ("/api/events", "/api/timeline", "/api/compare"):
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(base + route)
            assert excinfo.value.code == 409, route


def test_m16_serve_refuses_foreign_host(tmp_path):
    """PRA-M16: a request with a foreign Host header (DNS-rebinding) is refused,
    so a malicious page cannot read unblinded operator data from the browser."""
    _fixture_experiment(tmp_path)
    with serve_experiment(tmp_path) as base:
        req = urllib.request.Request(base + "/api/status", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req)
        assert excinfo.value.code == 403


def test_ac6_operator_page_self_contained(tmp_path):
    from harness.serve.page import OPERATOR_PAGE

    tmp_path.mkdir(exist_ok=True)
    with serve_experiment(tmp_path) as base:
        with urllib.request.urlopen(base + "/") as resp:
            served = resp.read().decode("utf-8")
            assert resp.headers["Content-Type"].startswith("text/html")
    assert served == OPERATOR_PAGE

    # the dossier's self-containment needles, minus its script ban (live tool):
    # no external URI schemes, no fetched assets, no links out [AC-6]
    for needle in ("http://", "https://", "src=", "href=", "url(", "@import", "<link"):
        assert needle not in OPERATOR_PAGE, f"external/active reference {needle!r} in page"
    assert OPERATOR_PAGE.lstrip().startswith("<!doctype html>")
    # its only network calls are same-origin relative /api/ fetches (v2 routes
    # them through one helper; the URL literals stay relative)
    assert "await fetch(url)" in OPERATOR_PAGE
    for literal in ('"/api/experiments"', '"/api/status?exp="', '"/api/events?exp="'):
        assert literal in OPERATOR_PAGE, f"expected relative endpoint literal {literal}"


def test_ac6_operator_page_unblinded_disclosure(tmp_path):
    from harness.serve.page import OPERATOR_PAGE

    # the standing disclosure names both the unblinded status and the
    # EVAL-7 reviewer disqualification — on the page itself, every render [D003]
    assert "Unblinded operator view" in OPERATOR_PAGE
    assert "disqualified" in OPERATOR_PAGE
    assert "EVAL-7" in OPERATOR_PAGE
    assert "Read-only" in OPERATOR_PAGE


# --- AC-7: structural contracts ---------------------------------------------------
def test_ac7_observability_contracts_and_no_entrypoints():
    text = (_REPO / ".importlinter").read_text(encoding="utf-8")

    def section(name: str) -> str:
        assert f"[importlinter:contract:{name}]" in text, f"contract {name} missing"
        body = text.split(f"[importlinter:contract:{name}]", 1)[1]
        return body.split("[importlinter:contract:", 1)[0]

    harbor = section("harbor-confined-to-seam")
    for mod in ("harness.status", "harness.serve", "harness.run.heartbeat"):
        assert mod in harbor, f"{mod} missing from the harbor-confinement source list"
    ledger_contract = section("ledger-writes-only-via-events")
    for mod in ("harness.status", "harness.serve"):
        assert mod in ledger_contract, f"{mod} missing from the ledger-writes source list"
    obs = section("observability-llm-free")
    assert "harness.judge.providers" in obs and "harness.judge.client" in obs

    # importing the observability modules registers no entrypoint and no event kind
    import harness.run.heartbeat  # noqa: F401
    import harness.serve  # noqa: F401
    import harness.status  # noqa: F401
    from harness.entrypoints import all_entrypoints
    from harness.ledger.events import REGISTERED_EVENTS

    names = {e.name for e in all_entrypoints()}
    assert not {n for n in names if "status" in n or "serve" in n or "heartbeat" in n}
    assert not {k for k in REGISTERED_EVENTS if "heartbeat" in k or "status" in k}

    # the new contract is load-bearing: a planted judge-client import in
    # harness.status must break it (reproduce-first pattern, XC-5 precedent)
    from tests.fixtures.lint import run_lint

    module = _REPO / "harness" / "status" / "aggregate.py"
    original = module.read_text(encoding="utf-8")
    planted = (
        original
        + "\n\ndef _planted_contract_violation():  # test-injected, restored below\n"
        + "    import harness.judge.client  # noqa\n"
    )
    try:
        module.write_text(planted, encoding="utf-8")
        result = run_lint()
        assert result.returncode != 0, "planted judge-client import broke no contract"
        assert "observability-llm-free" in result.stdout or "Read-only observability" in result.stdout, result.stdout
    finally:
        module.write_text(original, encoding="utf-8")


def test_m_t2_status_refuses_nonexistent_directory(tmp_path):
    """F-M-T2: `bench status <typo>` previously rendered a plausible healthy
    'chain OK (empty)' snapshot named after the typo'd basename. A directory
    that does not exist must refuse (exit 2); an existing directory with no
    ledger still legitimately renders the empty state."""
    from harness.cli import app

    runner = CliRunner()
    r = runner.invoke(app, ["status", str(tmp_path / "no-such-exp")])
    assert r.exit_code == 2
    assert "no such experiment directory" in (r.output + (r.stderr or ""))

    empty = tmp_path / "real-but-empty"
    empty.mkdir()
    r2 = runner.invoke(app, ["status", str(empty)])
    assert r2.exit_code == 0
    assert "chain    OK" in r2.output


def test_m_i1_same_size_tamper_with_restored_mtime_fails_closed(tmp_path):
    """F-M-I1: the verify-cache key is a content hash. A same-size rewrite with
    os.utime() restoring the mtime defeated the old (size, mtime_ns) signature —
    a warmed cache kept serving tampered events with a stale ok verdict."""
    import os

    ledger = _fixture_experiment(tmp_path)
    with serve_experiment(tmp_path) as base:
        urllib.request.urlopen(base + "/api/events")  # warm the verify cache

        st = ledger.stat()
        data = ledger.read_bytes()
        # same-size corruption: swap one byte inside a recorded value
        idx = data.index(b'"event":"')
        tampered = data[:idx + 9] + bytes([data[idx + 9] ^ 0x01]) + data[idx + 10:]
        assert len(tampered) == len(data) and tampered != data
        ledger.write_bytes(tampered)
        os.utime(ledger, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/events")
        assert excinfo.value.code == 409
