"""EVAL-14 — operator UI v2, server side.

AC map here: workspace scan (AC-1), trial drill-down (AC-2), paired compare +
fence watermark (AC-6), fence checklist + artifacts (AC-7), posture under
growth (AC-8). The page-drive ACs (AC-3..AC-5) live in
``test_eval14_page_drive.py``. Spec: docs/design/specs/eval14.spec.md.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.judge.assemble import comparison_id_for
from harness.ledger import events as ledger_events
from harness.ledger.query import read_events
from harness.serve.compare import paired_comparisons
from harness.serve.server import make_server
from harness.serve.workspace import scan_workspace
from harness.status.trial import trial_detail
from tests.fixtures.builders import fixed_ctx, locked_experiment
from tests.fixtures.scenarios import rich_experiment


def _passing_fence(fx: dict) -> CorpusManifest:
    """Extend a rich experiment until every fence item is ok, returning the
    matching manifest: ledgered full-run-validated calibration, then a passing
    selfcheck AFTER all data events (currency), tasks admitted."""
    ledger, ctx, spec = fx["ledger"], fx["ctx"], fx["spec"]
    ledger_events.record_calibration_run(
        ledger, ctx, corpus_id=spec.corpus.id, semver=spec.corpus.version,
        kind="full", run={"n": 2}, status="full-run-validated",
    )
    ledger_events.record_selfcheck(
        ledger, ctx, selected_method="percentile", nominal=0.95, coverage=0.95,
        mc_interval=[0.9, 1.0], n_sim=8, n_boot=40, n_tasks=2,
        null_model="binary", passed=True,
    )
    return CorpusManifest(
        corpus_id=spec.corpus.id, semver=spec.corpus.version, kind="public",
        tasks=[TaskEntry(task_id=t, sha="0" * 64, status="admitted") for t in ["t1", "t2"]],
    )


def _serve(target, *, root=False):
    srv = make_server(None if root else target, root=target if root else None, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


def _get_json(url: str):
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _stop(srv, thread):
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


# --- AC-1: workspace scan ------------------------------------------------------
def test_ac1_workspace_scan_summaries(tmp_path):
    rich = rich_experiment(tmp_path / "exp-a")
    locked_experiment(tmp_path / "exp-b")  # planned, never run
    tampered_dir = tmp_path / "exp-tampered"
    locked_experiment(tampered_dir)
    ledger = tampered_dir / "ledger.ndjson"
    # a rewritten HEAD line is the chain's documented opacity boundary (that is
    # what external anchors cover) — give the tamper a successor so the broken
    # back-pointer is what the scan must catch
    ledger_events.record_cant_grade(
        ledger, fixed_ctx(experiment_id="exp-tampered"),
        trial_id="tampered-cover", reason="grader_unavailable",
    )
    lines = ledger.read_text(encoding="utf-8").splitlines()
    doctored = json.loads(lines[0])
    doctored["seed"] = 999999
    lines[0] = json.dumps(doctored, sort_keys=True, separators=(",", ":"))
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "not-an-experiment").mkdir()  # silently not one
    (tmp_path / "stray.txt").write_text("x", encoding="utf-8")

    rows = scan_workspace(tmp_path)
    assert [r["name"] for r in rows] == ["exp-a", "exp-b", "exp-tampered"]

    a = rows[0]
    assert a["chain"]["ok"] is True
    assert a["heartbeat_state"] == "finished"
    assert a["summary"]["cells"] == {"planned": 4, "done": 4, "infra_failures": 0}
    assert a["summary"]["grade"]["graded"] == 4
    assert a["summary"]["judge"] == {
        "verdicts": 1, "cant_judge": 0, "pairs_ready": 2, "pairs_expected": 2,
    }
    assert a["summary"]["quarantines"] == 1
    assert a["summary"]["last_event_ts"].startswith("2026-01-01T")

    b = rows[1]
    assert b["summary"]["locked"] is True and b["summary"]["cells"]["done"] == 0

    t = rows[2]
    assert t["chain"]["ok"] is False
    assert t["summary"] is None  # withheld, never zeros [fail closed]

    # the endpoint serves the same rows, and root mode demands exp= on scoped APIs
    srv, thread, base = _serve(tmp_path, root=True)
    try:
        assert _get_json(base + "/api/experiments")["experiments"] == json.loads(
            json.dumps(rows)
        )
        st = _get_json(base + "/api/status?exp=exp-a")
        assert st["experiment_id"] == "exp-a" and st["stages"] is not None
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/status")
        assert excinfo.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/status?exp=..%2Fexp-a")
        assert excinfo.value.code == 404  # name shape refused, never path-joined
    finally:
        _stop(srv, thread)


# --- AC-2: trial drill-down ------------------------------------------------------
def test_ac2_trial_detail_aggregates(tmp_path):
    fx = rich_experiment(tmp_path)
    flagged = fx["flagged"]  # t1/control: graded fail, flagged, in the judged pair

    d = trial_detail(tmp_path, flagged)
    assert d["record"]["task_id"] == "t1" and d["record"]["arm"] == "control"
    assert d["trajectory"]["status"] == "verified"
    kinds = [s["kind"] for s in d["trajectory"]["steps"]]
    assert kinds == ["message", "tool_call", "file_edit"]
    # null honesty: the native log has no per-step timings — null, never zero
    assert all(s["relative_ts"] is None for s in d["trajectory"]["steps"])
    assert d["grade"]["binary_score"] is False
    assert d["grade"]["grades"][0]["assertions"][0]["result"] == "fail"
    assert d["comparison_id"] == comparison_id_for("t1", 0)
    assert [v["winner"] for v in d["verdicts"]] == ["B"]
    assert d["forensics"]["flags"][0]["detector"] == "suspicious_single_step"
    assert d["forensics"]["metrics"] == {"steps": 3}
    assert d["quarantine"] is None
    # no metering proxy in the fixture run ⇒ attempts are unmeasured (None),
    # never fabricated as an empty list [EVAL-4-D004]
    assert d["egress"] == {"violation": False, "attempts": None}

    quarantined = fx["trial_ids"][("t2", "treatment")]
    dq = trial_detail(tmp_path, quarantined)
    assert dq["quarantine"] == {"reason": "fixture quarantine"}
    assert dq["verdicts"] == []  # t2 was never judged

    srv, thread, base = _serve(tmp_path)
    try:
        served = _get_json(base + f"/api/trial?id={flagged}")
        assert served == json.loads(json.dumps(d))
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/trial?id=trial-does-not-exist")
        assert excinfo.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/trial")
        assert excinfo.value.code == 400
    finally:
        _stop(srv, thread)


# --- AC-6: paired compare + fence watermark ----------------------------------------
def test_ac6_compare_pairs_diff_and_watermark(tmp_path):
    fx = rich_experiment(tmp_path)

    c = paired_comparisons(tmp_path)
    assert (c["arm_a"], c["arm_b"]) == ("control", "treatment")  # lock order
    assert c["summary"]["pairs"] == 2
    by_task = {p["task_id"]: p for p in c["pairs"]}
    t1, t2 = by_task["t1"], by_task["t2"]
    # deterministic and advisory tiers stay separate lines, never blended
    assert (t1["a"]["holdout_pass"], t1["b"]["holdout_pass"]) == (False, True)
    assert t1["judge"]["winner"] == "B"
    assert t1["disagreement"] is True
    assert t2["disagreement"] is False and t2["judge"] is None
    assert c["summary"]["holdout"] == {"a_only": 0, "b_only": 1, "both": 1, "neither": 0}
    assert c["summary"]["judge"] == {"a": 0, "b": 1, "tie": 0, "cant": 0, "unjudged": 1}
    assert c["summary"]["disagreements"] == 1
    # the workspace content differs per arm → real diff segments
    assert any(seg["op"] != "equal" and (seg["a"] or seg["b"]) for seg in t1["segments"])
    # holdout evidence rides each response
    assert t1["b"]["holdout_results"] == [{"id": "h1", "result": "pass"}]

    # watermark: EXPLORATORY until the official fence passes — same fence as analyze
    assert c["official_ready"] is False
    manifest = _passing_fence(fx)
    c2 = paired_comparisons(tmp_path, corpus_manifest=manifest)
    assert c2["official_ready"] is True


# --- AC-7: fence checklist + artifacts ------------------------------------------------
def test_ac7_fence_checklist_and_artifacts(tmp_path):
    from harness.analyze.fence import official_fence_report

    fx = rich_experiment(tmp_path)
    before = fx["ledger"].read_bytes()

    report = official_fence_report(tmp_path)
    states = {i["id"]: i["state"] for i in report["items"]}
    assert states["chain"] == "ok" and states["lock"] == "ok"
    assert states["corpus_identity"] == "unchecked"  # no manifest supplied
    assert states["calibration"] == "failed"  # nothing ledgered yet
    assert states["selfcheck"] == "failed"
    assert states["rubric"] == "ok" and states["contamination"] == "ok"
    assert report["official_ready"] is False
    assert fx["ledger"].read_bytes() == before  # side-effect-free: no cant_analyze

    manifest = _passing_fence(fx)
    report2 = official_fence_report(tmp_path, corpus_manifest=manifest)
    assert {i["id"]: i["state"] for i in report2["items"]} == {
        "chain": "ok", "lock": "ok", "corpus_identity": "ok", "corpus_coverage": "ok",
        "calibration": "ok", "rubric": "ok", "selfcheck": "ok", "contamination": "ok",
        "insulation": "ok",  # F-M-C3
    }
    assert report2["official_ready"] is True

    # artifacts: fixed-name allowlist, exact bytes, no rendering by the UI
    (tmp_path / "findings.json").write_text('{"fixture": true}', encoding="utf-8")
    (tmp_path / "findings.exploratory.dossier.html").write_text(
        "<!doctype html><p>fixture dossier</p>", encoding="utf-8"
    )
    srv, thread, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/artifact?name=findings.json") as resp:
            assert resp.read() == b'{"fixture": true}'
            assert resp.headers["Content-Type"] == "application/json"
        with urllib.request.urlopen(
            base + "/artifact?name=findings.exploratory.dossier.html"
        ) as resp:
            assert resp.headers["Content-Type"].startswith("text/html")
        for bad in ("..%2Fledger.ndjson", "ledger.ndjson", "findings.other.html", ""):
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(base + "/artifact?name=" + bad)
            assert excinfo.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/artifact?name=findings.official.dossier.html")
        assert excinfo.value.code == 404  # allowlisted but not rendered: honest 404
    finally:
        _stop(srv, thread)
    kinds = [e["event"] for e in read_events(fx["ledger"])]
    assert "cant_analyze" not in kinds and "findings_rendered" not in kinds


# --- AC-8: posture preserved under growth ------------------------------------------
def _dir_digest(root: Path) -> list[tuple[str, str]]:
    return sorted(
        (str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*") if p.is_file()
    )


def test_ac8_posture_all_routes(tmp_path):
    from harness.serve.page import OPERATOR_PAGE

    fx = rich_experiment(tmp_path / "exp-a")
    before = _dir_digest(tmp_path)
    srv, thread, base = _serve(tmp_path, root=True)
    try:
        # the page stays self-contained and carries the standing disclosure
        with urllib.request.urlopen(base + "/") as resp:
            page = resp.read().decode("utf-8")
        assert page == OPERATOR_PAGE
        for needle in ("http://", "https://", "src=", "href=", "url(", "@import", "<link"):
            assert needle not in page, f"external/active reference {needle!r}"
        assert "Unblinded operator view" in page and "disqualified" in page

        # browse every read route, then prove nothing changed on disk
        for route in (
            "/api/experiments", "/api/status?exp=exp-a", "/api/events?exp=exp-a&offset=0",
            "/api/timeline?exp=exp-a", "/api/compare?exp=exp-a", "/api/fence?exp=exp-a",
            f"/api/trial?exp=exp-a&id={fx['flagged']}",
        ):
            _get_json(base + route)
        assert _dir_digest(tmp_path) == before

        # GET-only on the new routes too
        for route in ("/api/experiments", "/api/compare?exp=exp-a", "/artifact?name=x"):
            req = urllib.request.Request(base + route, method="POST", data=b"")
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(req)
            assert excinfo.value.code == 405
            assert excinfo.value.headers["Allow"] == "GET"
    finally:
        _stop(srv, thread)

    # structural posture: contracts cover the observability packages; no new
    # event kind and no entrypoint arrived with this story
    text = (Path(__file__).resolve().parents[1] / ".importlinter").read_text("utf-8")
    for contract in ("harbor-confined-to-seam", "ledger-writes-only-via-events",
                     "observability-llm-free"):
        section = text.split(f"[importlinter:contract:{contract}]", 1)[1].split(
            "[importlinter:contract:", 1
        )[0]
        assert "harness.serve" in section and "harness.status" in section
    from harness.entrypoints import all_entrypoints
    from harness.ledger.events import REGISTERED_EVENTS

    assert not {n.name for n in all_entrypoints() if "serve" in n.name or "workspace" in n.name}
    assert not {k for k in REGISTERED_EVENTS if "compare" in k or "fence" in k or "workspace" in k}
