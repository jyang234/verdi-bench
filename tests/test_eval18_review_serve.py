"""EVAL-18 — reviewer surface: blinded capture-then-reveal over HTTP.

AC map: structural isolation + leak refusal (AC-1), capture-then-reveal one
event each (AC-2), mutation posture (AC-3), queue keyboard ergonomics (AC-4),
dual-server isolation with verbatim packet bytes (AC-5).
Spec: docs/design/specs/eval18.spec.md.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger.query import find_events, read_events
from harness.review.serve import DEFAULT_HOST, make_review_server
from tests.fixtures.browser import drive
from tests.fixtures.builders import fixed_ctx, seed_trial_and_grade, write_experiment_yaml

runner = CliRunner()
_ARMS = ("control", "treatment")


def reviewed_experiment(expdir: Path, *, tasks: int = 2) -> Path:
    """Plan → seed graded trials → fake judge → build the packet: the exact
    pipeline a CLI reviewer would inherit.

    Grades are arranged so every comparison is a judge-vs-deterministic
    disagreement (the fake judge ties on holdout pass *counts* while the
    binary rates favor control), putting all of them in EVAL-7's mandatory
    stratum — the queue holds exactly ``tasks`` comparisons, not a sampled
    floor subset."""
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(
        expdir / "experiment.yaml",
        judge={"model": "fake/deterministic-2026-01-01", "rubric": "rubric.md",
               "orders": "both", "temperature": 0},
    )
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    task_list = [{"id": f"t{i}", "prompt": "solve"} for i in range(1, tasks + 1)]
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": task_list}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    ctx = fixed_ctx(experiment_id=expdir.name)
    for t in task_list:
        seed_trial_and_grade(ledger, ctx, trial_id=f"a-{t['id']}", task_id=t["id"],
                             arm="control", passed=True)
        seed_trial_and_grade(ledger, ctx, trial_id=f"b-{t['id']}", task_id=t["id"],
                             arm="treatment", passed=False,
                             assertions=[
                                 {"id": "h1", "source": "holdout_test", "result": "pass"},
                                 {"id": "h2", "source": "holdout_test", "result": "fail"},
                             ])
    assert runner.invoke(app, ["judge", str(expdir)]).exit_code == 0
    r = runner.invoke(app, ["review", "build", str(expdir)])
    assert r.exit_code == 0, r.output
    return ledger


def _serve(expdir: Path, reviewer: str = "alice"):
    srv = make_review_server(expdir, reviewer=reviewer, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


def _stop(srv, thread):
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _get(base: str, path: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(base + path) as resp:
        return resp.status, resp.read()


def _post(base: str, path: str, body: dict, *, headers: dict | None = None):
    # A real same-origin browser fetch carries Origin + application/json; the
    # CSRF guard (PRA-H2) requires both. Callers override to exercise refusals.
    hdrs = {"Content-Type": "application/json", "Origin": base}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers=hdrs)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# --- AC-1: structural isolation + leak refusal --------------------------------------
def test_ac1_isolation_routes_imports_scrub(tmp_path):
    expdir = tmp_path / "exp"
    reviewed_experiment(expdir)
    srv, thread, base = _serve(expdir)
    try:
        # the operator tier does not exist on this server
        for route in ("/api/status", "/api/events", "/api/timeline", "/api/compare",
                      "/api/trial", "/api/experiments", "/api/fence", "/artifact"):
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(base + route)
            assert excinfo.value.code == 404

        # clean packet serves; a poisoned one is refused with the reason
        _, packet = _get(base, "/packet")
        assert packet.startswith(b"<!doctype html>")
        packet_path = expdir / "review_packet.html"
        original = packet_path.read_text(encoding="utf-8")
        packet_path.write_text(original + "\n<!-- control -->", encoding="utf-8")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/packet")
        assert excinfo.value.code == 409
        assert "leaking packet" in json.loads(excinfo.value.read())["error"]
        packet_path.write_text(original, encoding="utf-8")
    finally:
        _stop(srv, thread)

    # the isolation is a contract, and it is load-bearing: a planted operator
    # import into the review subsystem breaks lint [the XC-5 plant pattern]
    text = (Path(__file__).resolve().parents[1] / ".importlinter").read_text("utf-8")
    section = text.split("[importlinter:contract:reviewer-surface-isolated]", 1)[1].split(
        "[importlinter:contract:", 1)[0]
    for mod in ("harness.serve", "harness.status", "harness.author"):
        assert mod in section
    from tests.test_import_contracts import _run_lint

    module = Path(__file__).resolve().parents[1] / "harness" / "review" / "serve.py"
    original = module.read_text(encoding="utf-8")
    try:
        module.write_text(
            original + "\n\ndef _planted_contract_violation():  # test-injected\n"
                       "    import harness.serve  # noqa\n",
            encoding="utf-8",
        )
        result = _run_lint()
        assert result.returncode != 0
        assert "reviewer surface" in result.stdout or "reviewer-surface" in result.stdout
    finally:
        module.write_text(original, encoding="utf-8")


# --- AC-2: capture then reveal, one event each ----------------------------------------
def test_ac2_capture_then_reveal_one_event_each(tmp_path):
    expdir = tmp_path / "exp"
    ledger = reviewed_experiment(expdir)
    srv, thread, base = _serve(expdir, reviewer="blind-bob")
    try:
        # pre-verdict, nothing served carries an arm string
        _, queue_raw = _get(base, "/api/queue")
        _, page = _get(base, "/")
        _, packet = _get(base, "/packet")
        for arm in _ARMS:
            assert arm.encode() not in queue_raw
            assert arm.encode() not in page
            assert arm.encode() not in packet
        queue = json.loads(queue_raw)
        assert len(queue["pending"]) == 2 and queue["done"] == []

        cid = queue["pending"][0]["comparison_id"]
        # the reveal refuses before the verdict — the record-layer gate, served
        code, err = _post(base, "/api/reveal", {"comparison_id": cid})
        assert code == 409 and err["error_class"] == "RevealError"

        before = len(read_events(ledger))
        code, ok = _post(base, "/api/verdict", {
            "comparison_id": cid, "winner": "2", "reason": "response 2 is correct",
            "arm_recognized": True, "arm_guess": "the newer model",
        })
        assert code == 200
        # no arm name in the response — the frame translation stays internal
        for arm in _ARMS:
            assert arm not in json.dumps(ok)
        evs = read_events(ledger)
        assert len(evs) == before + 1  # exactly one event for the capture
        hv = evs[-1]
        assert hv["event"] == "human_verdict"
        assert hv["provenance"]["actor"] == "blind-bob"
        assert hv["integrity"] == {
            "arm_recognized": True, "arm_guess": "the newer model",
            "actual_arm": hv["integrity"]["actual_arm"],  # the ledgered map's truth
        }
        assert hv["integrity"]["actual_arm"] in _ARMS
        assert hv["verdict"]["source"] == "human"

        # duplicate capture refused by the record layer
        code, err = _post(base, "/api/verdict", {
            "comparison_id": cid, "winner": "1", "arm_recognized": False,
        })
        assert code == 409

        # the reveal: its own explicit act, exactly one more event
        code, rev = _post(base, "/api/reveal", {"comparison_id": cid})
        assert code == 200 and set(rev["revealed"]) == {"judge_verdict_id", "arm_identities"}
        assert len(read_events(ledger)) == before + 2
        assert read_events(ledger)[-1]["event"] == "reveal"
        code, _ = _post(base, "/api/reveal", {"comparison_id": cid})
        assert code == 409  # one unblinding per comparison [RV-8]
    finally:
        _stop(srv, thread)


# --- AC-3: mutation posture ---------------------------------------------------------
def test_ac3_mutation_posture(tmp_path, monkeypatch):
    expdir = tmp_path / "exp"
    reviewed_experiment(expdir)

    # the reviewer binds at launch or the verb refuses — never "unknown"
    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: (_ for _ in ()).throw(OSError("no user")))
    result = runner.invoke(app, ["review", "serve", str(expdir)])
    assert result.exit_code == 2 and "--actor" in result.output
    assert DEFAULT_HOST == "127.0.0.1"

    srv, thread, base = _serve(expdir)
    try:
        digest_before = sorted(
            (str(p.relative_to(expdir)), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in expdir.rglob("*") if p.is_file()
        )
        for route in ("/", "/api/queue", "/packet"):
            _get(base, route)
        digest_after = sorted(
            (str(p.relative_to(expdir)), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in expdir.rglob("*") if p.is_file()
        )
        assert digest_after == digest_before  # GETs are side-effect-free

        code, _ = _post(base, "/api/queue", {})
        assert code == 404  # only the two capture endpoints accept POST
        req = urllib.request.Request(base + "/api/verdict", data=b"", method="PUT")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req)
        assert excinfo.value.code == 405
    finally:
        _stop(srv, thread)

    # no new event kinds arrived with this story
    from harness.ledger.events import REGISTERED_EVENTS

    assert not {k for k in REGISTERED_EVENTS if "review_serve" in k or "queue" in k}


# --- AC-4: queue keyboard ergonomics ---------------------------------------------------
def test_ac4_queue_keyboard_drive(tmp_path):
    from harness.review.serve_page import REVIEWER_PAGE

    for needle in ("http://", "https://", "src=", "href=", "url(", "@import", "<link"):
        assert needle not in REVIEWER_PAGE, f"external/active reference {needle!r}"
    assert "Do not open" in REVIEWER_PAGE and "operator view" in REVIEWER_PAGE

    expdir = tmp_path / "exp"
    ledger = reviewed_experiment(expdir)
    srv, thread, base = _serve(expdir, reviewer="kbd-reviewer")
    try:
        body = """
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  out.start = await page.evaluate(() => window.__vb());
  // pick a winner by hotkey; submit stays blocked until integrity is answered
  await page.keyboard.press('2');
  await page.waitForTimeout(300);
  out.picked = await page.evaluate(() => window.__vb());
  await page.keyboard.press('Enter');
  await page.waitForTimeout(600);
  out.blockedSubmit = await page.evaluate(() => window.__vb());
  // answer integrity (cannot identify), then Enter records and advances
  await page.evaluate(() => { [...document.querySelectorAll('label.q input')][1].click(); });
  await page.waitForTimeout(300);
  out.answered = await page.evaluate(() => window.__vb());
  await page.keyboard.press('Enter');
  await page.waitForTimeout(1500);
  out.advanced = await page.evaluate(() => window.__vb());
  // CANT_JUDGE is always reachable on the next comparison
  await page.keyboard.press('c');
  await page.waitForTimeout(300);
  out.cant = await page.evaluate(() => window.__vb());
  out.progress = await page.evaluate(() => document.body.textContent.includes('1 of 2 recorded'));
"""
        out = drive(base, body, tmp_path)
        assert out["start"]["pending"] == 2 and out["start"]["sel"]
        assert out["picked"]["winner"] == "2" and out["picked"]["canSubmit"] is False
        assert out["blockedSubmit"]["pending"] == 2  # Enter without integrity: no event
        assert out["answered"]["recognized"] is False and out["answered"]["canSubmit"] is True
        assert out["advanced"]["pending"] == 1 and out["advanced"]["done"] == 1
        assert out["advanced"]["sel"] != out["start"]["sel"]  # auto-advance
        assert out["cant"]["winner"] == "CANT_JUDGE"
        assert out["progress"] is True
        assert out["__errors"] == []
        hv = find_events(ledger, "human_verdict")
        assert len(hv) == 1 and hv[0]["provenance"]["actor"] == "kbd-reviewer"
    finally:
        _stop(srv, thread)


# --- AC-5: dual-server isolation, verbatim packet bytes ----------------------------------
def test_ac5_dual_server_isolation(tmp_path):
    from harness.serve.server import make_server

    expdir = tmp_path / "exp"
    reviewed_experiment(expdir)

    operator = make_server(expdir, port=0)
    op_thread = threading.Thread(target=operator.serve_forever, daemon=True)
    op_thread.start()
    op_base = f"http://127.0.0.1:{operator.server_address[1]}"
    srv, thread, base = _serve(expdir)
    try:
        # every reviewer route stays arm-free while the operator, over the SAME
        # experiment, legitimately serves identities
        for route in ("/", "/api/queue", "/packet"):
            _, body = _get(base, route)
            for arm in _ARMS:
                assert arm.encode() not in body, f"{arm} leaked via {route}"
        with urllib.request.urlopen(op_base + "/api/status") as resp:
            op_status = resp.read()
        assert b"control" in op_status  # the unblinded tier, unchanged

        # the packet served is the built file's bytes, verbatim [D004]
        _, served = _get(base, "/packet")
        assert served == (expdir / "review_packet.html").read_bytes()
    finally:
        _stop(srv, thread)
        operator.shutdown()
        operator.server_close()
        op_thread.join(timeout=5)
