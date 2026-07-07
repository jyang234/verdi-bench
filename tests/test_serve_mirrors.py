"""Python↔JS logic mirrors on the operator surface stay honest [refactor 07 §4].

Three places in the operator SPA re-implement Python logic that lives elsewhere.
The refactor decision (P5-JS) was: ship the derived value server-side where the
payload allows, otherwise pin the mirror with a parity test that computes both
sides and fails on divergence. For all three here the value is *client-side by
nature* (an offline bundle's dispatch; an overview strip that polls status+events
rather than the compare payload; a hardcoded id format), so shipping it would
change the fetch shape or touch an off-limits subsystem — a parity test is the
right guard. These fail the moment the Python side drifts from the JS mirror.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness.judge.assemble import comparison_id_for
from harness.ledger.query import tail_events
from harness.serve.compare import paired_comparisons
from tests.fixtures.scenarios import rich_experiment

_SERVE = Path(__file__).resolve().parent.parent / "harness" / "serve"
_APP_JS = (_SERVE / "static" / "app.js").read_text(encoding="utf-8")
_SERVER_PY = (_SERVE / "server.py").read_text(encoding="utf-8")


# --- Mirror A: the comparison-id format (parity test) ------------------------------
def test_comparison_id_format_is_the_serve_page_mirror():
    """``pairTallies``/``pairVerdict`` look verdicts up by reconstructing
    ``"cmp-<task>-r<rep>"`` — the judge's ``comparison_id_for`` format. Pin the
    Python contract AND the two JS call sites: if the format ever changes, this
    assertion breaks and the literal must move in lockstep. (Shipping the id
    instead would mean enlarging the status/events payload with a per-cell
    id map the overview does not otherwise need.)"""
    assert comparison_id_for("t1", 0) == "cmp-t1-r0"
    assert comparison_id_for("abc-2", 3) == "cmp-abc-2-r3"
    assert '"cmp-" + task + "-r" + rep' in _APP_JS  # pairTallies
    assert '"cmp-" + taskId + "-r" + rep' in _APP_JS  # pairVerdict


# --- Mirror B: pairTallies mirrors compare.py's summary arithmetic (parity test) ---
def _pair_tallies(events, arm_a, arm_b):
    """The Python mirror of ``static/app.js`` ``pairTallies`` — the overview
    strip's client-side tally over status+events. This is the test's stand-in
    for the JS side; the assertion below fails if ``compare.py`` diverges."""
    trials, grades, winners = {}, {}, {}
    for ev in events:
        if ev["event"] == "trial":
            r = ev["trial_record"]
            trials[(r["task_id"], r["repetition"], r["arm"])] = r["trial_id"]
        elif ev["event"] == "grade":
            grades[ev["trial_id"]] = ev["binary_score"]
        elif ev["event"] == "judge_verdict":
            v = ev["verdict"]
            winners[v["comparison_id"]] = v.get("winner")
    cells = {(task, rep) for (task, rep, _arm) in trials}
    t = dict(a=0, b=0, both=0, neither=0, graded_pairs=0, pairs=0,
             ja=0, jb=0, jtie=0, jcant=0, junjudged=0)
    for task, rep in cells:
        ta = trials.get((task, rep, arm_a))
        tb = trials.get((task, rep, arm_b))
        if ta is None or tb is None:
            continue
        t["pairs"] += 1
        a, b = grades.get(ta), grades.get(tb)
        if a is not None and b is not None:
            t["graded_pairs"] += 1
            if a and b:
                t["both"] += 1
            elif a:
                t["a"] += 1
            elif b:
                t["b"] += 1
            else:
                t["neither"] += 1
        w = winners.get(f"cmp-{task}-r{rep}")
        if w == "A":
            t["ja"] += 1
        elif w == "B":
            t["jb"] += 1
        elif w == "TIE":
            t["jtie"] += 1
        elif w == "CANT_JUDGE":
            t["jcant"] += 1
        else:
            t["junjudged"] += 1
    return t


def test_pair_tallies_mirror_matches_compare_summary(tmp_path):
    rich_experiment(tmp_path / "exp")
    exp_dir = tmp_path / "exp"
    pc = paired_comparisons(exp_dir)
    events, _ = tail_events(exp_dir / "ledger.ndjson", 0)
    t = _pair_tallies(events, pc["arm_a"], pc["arm_b"])
    s = pc["summary"]
    # the mirror must be non-trivial (else the parity is vacuous)
    assert t["pairs"] > 0 and (t["a"] or t["b"] or t["both"] or t["neither"])
    assert t["pairs"] == s["pairs"]
    assert (t["a"], t["b"], t["both"], t["neither"]) == (
        s["holdout"]["a_only"], s["holdout"]["b_only"],
        s["holdout"]["both"], s["holdout"]["neither"],
    )
    assert (t["ja"], t["jb"], t["jtie"], t["jcant"], t["junjudged"]) == (
        s["judge"]["a"], s["judge"]["b"], s["judge"]["tie"],
        s["judge"]["cant"], s["judge"]["unjudged"],
    )


# --- Mirror C: bundleData mirrors the live /api routes (parity test) ----------------
def test_bundle_data_route_mirror_covers_the_live_data_routes():
    """``bundleData`` dispatches the offline snapshot the same paths the live
    server serves — there is no server value to ship, the whole point is
    transport-free client dispatch. Assert the route sets agree: a new ``/api``
    data route on one side but not the other is caught here."""
    server_routes = set(re.findall(r'"(/[^"]*)":\s*lambda', _SERVER_PY))
    # extraction sanity: a refactor that drops the lambda idiom fails loudly here
    assert {"/", "/api/status", "/artifact"} <= server_routes, server_routes
    # the page/asset routes a static bundle deliberately does not embed
    data_routes = server_routes - {"/", "/favicon.ico", "/artifact"}
    bundle_routes = set(re.findall(r'path === "(/api/[^"]*)"', _APP_JS))
    assert data_routes == bundle_routes, (data_routes, bundle_routes)
    assert {"/api/compare", "/api/fence", "/api/experiments"} <= bundle_routes
