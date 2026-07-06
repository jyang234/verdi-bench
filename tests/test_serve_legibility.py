"""Operator-page legibility sweep — honesty of failure states + information
surfacing.

Pins the UX-audit fixes over the EVAL-14/19 operator page:

* fail-closed rendering: a broken chain WITHHOLDS the trials/compare/findings
  screens (never "no trials yet"), keeps the observer's own status truthful
  ("live", not "unreachable"), and never blows the layout out horizontally;
* null honesty: an ungraded holdout renders neutral, never failure-red; a
  forensics-covered-but-clean trial is distinguished from "never scanned";
* surfacing: home rows carry arms/models and truthful lifecycle states, the
  overview carries a result-so-far strip that mirrors compare's banner, feed
  rows summarize every ledgered kind and deep-link, trial detail shows the
  record's telemetry and its pair's advisory verdict, trials sort by URL
  state.

Browser drives skip honestly without the node/playwright/chromium stack
(the docker-marker precedent), matching the rest of the UI acceptance suite.
"""

from __future__ import annotations

import threading

import yaml

from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
from harness.judge.assemble import comparison_id_for
from harness.ledger import events as ledger_events
from harness.serve.server import make_server
from harness.serve.workspace import scan_workspace
from harness.status.aggregate import compute_status
from tests.fixtures.browser import drive
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade
from tests.test_eval14_observability_ui import rich_experiment


def _serve_root(root):
    srv = make_server(None, root=root, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


def _stop(srv, thread):
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _record_trial(ledger, ctx, *, trial_id, task_id, arm, repetition=0, telemetry=None):
    """A trial event with NO grade — the ungraded-pair ingredient."""
    rec = TrialRecord.assemble(
        trial_id=trial_id, task_id=task_id, arm=arm, repetition=repetition,
        outcome=Outcome.completed, telemetry=Telemetry(**(telemetry or {})),
        provenance=Provenance(), flags=Flags(),
        artifacts_path=f"/tmp/{trial_id}/artifacts",
    )
    ledger_events.record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    return rec


def _paired_fixture(dirpath):
    """t1 graded on both arms (control fail 0.9, treatment pass 0.3) with an
    advisory B verdict; t2 has both trials but NO grades — an ungraded pair."""
    spec, _sp, ledger = locked_experiment(dirpath, repetitions=1)
    (dirpath / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}, {"id": "t2", "prompt": "p"}]}),
        encoding="utf-8",
    )
    ctx = fixed_ctx(experiment_id=dirpath.name)
    seed_trial_and_grade(ledger, ctx, trial_id="trial-a1", task_id="t1", arm="control",
                         passed=False, telemetry={"cost": 0.9, "wall_time_s": 120.0})
    seed_trial_and_grade(ledger, ctx, trial_id="trial-b1", task_id="t1", arm="treatment",
                         passed=True, telemetry={"cost": 0.3, "wall_time_s": 200.0})
    _record_trial(ledger, ctx, trial_id="trial-a2", task_id="t2", arm="control")
    _record_trial(ledger, ctx, trial_id="trial-b2", task_id="t2", arm="treatment")
    ledger_events.append_verdict(ledger, ctx, verdict={
        "comparison_id": comparison_id_for("t1", 0), "winner": "B", "reason": "r",
        "provenance": {"judge_model": "google/gemini-1.5-pro-002", "rubric_sha256": "0" * 64},
    })
    return spec, ledger, ctx


def _tampered_fixture(dirpath):
    """A locked experiment with one trial whose ledger then loses chain
    integrity (a rewritten byte) — the withheld-everywhere state."""
    _paired_fixture(dirpath)
    ledger = dirpath / "ledger.ndjson"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert '"tester"' in lines[1], "fixture expects the actor on the trial line"
    lines[1] = lines[1].replace('"tester"', '"intruder"', 1)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- server-side: additive home-row / status fields --------------------------------
def test_workspace_row_carries_arms_models_and_heartbeat_ts(tmp_path):
    rich_experiment(tmp_path / "exp-a")
    rows = scan_workspace(tmp_path)
    (row,) = rows
    assert row["summary"]["arms"] == ["control", "treatment"]
    assert set(row["summary"]["arm_models"]) == {"control", "treatment"}
    # the heartbeat's own ts rides along so the HOME screen can judge a silent
    # "running" doc stale exactly like the experiment screen does
    assert row["heartbeat_state"] == "finished" and row["heartbeat_ts"]


def test_status_spec_summary_names_arm_models(tmp_path):
    _paired_fixture(tmp_path)
    st = compute_status(tmp_path)["stages"]
    assert st["spec"]["arms"] == ["control", "treatment"]
    assert st["spec"]["arm_models"]["control"].startswith("anthropic/")
    assert st["spec"]["arm_models"]["treatment"].startswith("openai/")


# --- fail-closed rendering on a broken chain ---------------------------------------
def test_broken_chain_withholds_every_screen_without_lying(tmp_path):
    _tampered_fixture(tmp_path / "exp-t")
    srv, thread, base = _serve_root(tmp_path)
    try:
        # NOTE: assertions read the RENDERED #app / #bar nodes, never
        # document.body.textContent — body text includes the page's own
        # <script> source, which contains most UI literals.
        body = """
  await page.goto(BASE + '/#/experiments', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.home = await page.evaluate(() => {
    const row = [...document.querySelectorAll('tr.row')][0];
    return { state: row.cells[1].textContent, cells: row.cells[3].textContent };
  });

  await page.goto(BASE + '/#/exp/exp-t', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.exp = await page.evaluate(() => ({
    withheld: document.getElementById('app').textContent.includes('Ledger content withheld'),
    barChips: [...document.querySelectorAll('#bar .chip')].map(c => c.textContent),
    width: document.documentElement.scrollWidth }));

  await page.goto(BASE + '/#/exp/exp-t/trials', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.trials = await page.evaluate(() => ({
    withheld: document.getElementById('app').textContent.includes('Ledger content withheld'),
    noTrialsLie: document.getElementById('app').textContent.includes('No trials on this ledger'),
    rows: document.querySelectorAll('tr.row').length }));

  await page.goto(BASE + '/#/exp/exp-t/compare', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.compare = await page.evaluate(() => ({
    withheld: document.getElementById('app').textContent.includes('Ledger content withheld'),
    assembling: document.getElementById('app').textContent.includes('assembling comparisons') }));
"""
        out = drive(base, body, tmp_path)
        assert "chain broken" in out["home"]["state"]
        assert out["home"]["cells"] == "withheld"  # fail closed, never zeros
        # withheld card, no horizontal blowout, and the observer stays "live":
        # a served 409 is a data state, not an unreachable server
        assert out["exp"]["withheld"] is True
        assert "live" in out["exp"]["barChips"]
        assert not any(c.startswith("unreachable") for c in out["exp"]["barChips"])
        assert out["exp"]["width"] <= 1250
        # the trials screen must never describe withheld evidence as absence
        assert out["trials"]["withheld"] is True
        assert out["trials"]["noTrialsLie"] is False and out["trials"]["rows"] == 0
        assert out["compare"]["withheld"] is True and out["compare"]["assembling"] is False
        # the 409s are the point of this scenario: Chromium logs each refused
        # /api read as a console resource error; anything ELSE is a real bug
        assert all("409" in e and e.startswith("console:") for e in out["__errors"]), out["__errors"]
    finally:
        _stop(srv, thread)


# --- home rows: arms, truthful lifecycle states, denominators ----------------------
def test_home_rows_surface_arms_states_and_denominators(tmp_path):
    rich_experiment(tmp_path / "exp-a")
    # locked with a plan but nothing run: "ready · locked", never "unplanned"
    locked_experiment(tmp_path / "exp-fresh", repetitions=1)
    (tmp_path / "exp-fresh" / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8")
    srv, thread, base = _serve_root(tmp_path)
    try:
        # assertions read the rendered #app subtree — body.textContent would
        # also match the page's own <script> source
        body = """
  await page.goto(BASE + '/#/experiments', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.home = await page.evaluate(() => {
    const app = document.getElementById('app').textContent;
    const rows = [...document.querySelectorAll('tr.row')];
    return {
      headers: [...document.querySelectorAll('th')].map(t => t.textContent),
      arms: app.includes('control vs treatment'),
      models: app.includes('claude-3-5-sonnet'),
      states: rows.map(r => r.cells[1].textContent),
      gradedCell: rows[0].cells[6].textContent,
      updated: rows[0].cells[9].textContent,
    };
  });
"""
        out = drive(base, body, tmp_path)
        assert "arms" in out["home"]["headers"]
        assert out["home"]["arms"] is True and out["home"]["models"] is True
        # locked-with-a-plan reads "ready · locked", never the old "unplanned"
        assert out["home"]["states"] == ["finished", "ready · locked"]
        assert out["home"]["gradedCell"].startswith("4/4")  # graded / cells done
        assert out["home"]["updated"].endswith("ago")  # relative, absolute in title
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- overview: result strip mirrors compare; stages navigate; feed deep-links ------
def test_overview_result_strip_stage_nav_and_feed_links(tmp_path):
    fx = rich_experiment(tmp_path / "exp-a")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-a', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  out.strip = await page.evaluate(() => {
    const app = document.getElementById('app').textContent;
    return { present: app.includes('Result so far'),
             lead: app.includes('treatment leads'),
             advisory: app.includes('Judge (ADVISORY)') };
  });
  out.feed = await page.evaluate(() => {
    const items = [...document.querySelectorAll('#feedbox li')];
    const q = items.find(li => li.textContent.includes('forensic_quarantine'));
    return { quarantineSummary: q ? q.textContent : '',
             clickable: items.filter(li => li.className.includes('click')).length };
  });
  // a grade feed row deep-links to that trial's grade tab
  await page.evaluate(() => {
    [...document.querySelectorAll('#feedbox li.click')]
      .find(li => li.textContent.includes(' grade') || li.textContent.includes('grade'))
      .click();
  });
  await page.waitForTimeout(700);
  out.afterFeedClick = await page.evaluate(() => location.hash);

  // the judge stage card is a doorway to compare
  await page.goto(BASE + '/#/exp/exp-a', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    [...document.querySelectorAll('.stage.click')]
      .find(s => s.textContent.includes('judge')).click();
  });
  await page.waitForTimeout(600);
  out.afterStageClick = await page.evaluate(() => location.hash);
"""
        out = drive(base, body, tmp_path)
        # t1: control fail / treatment pass; t2: both pass -> treatment 2/2 leads
        assert out["strip"] == {"present": True, "lead": True, "advisory": True}
        # the quarantine row says WHICH trial and WHY, not a blank line
        assert fx["trial_ids"][("t2", "treatment")] in out["feed"]["quarantineSummary"]
        assert "fixture quarantine" in out["feed"]["quarantineSummary"]
        assert out["feed"]["clickable"] > 0
        assert "/trial/" in out["afterFeedClick"] and "tab=grade" in out["afterFeedClick"]
        assert out["afterStageClick"] == "#/exp/exp-a/compare"
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- trials: sortable by URL state, units in headers, verdict in the panel ---------
def test_trials_sort_units_and_panel_verdict(tmp_path):
    _paired_fixture(tmp_path / "exp-p")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-p/trials', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.headers = await page.evaluate(() => [...document.querySelectorAll('th')].map(t => t.textContent));
  // click the cost header: desc first, nulls last, state in the URL
  await page.evaluate(() => {
    [...document.querySelectorAll('th.sortable')].find(t => t.textContent.startsWith('cost')).click();
  });
  await page.waitForTimeout(600);
  out.sorted = await page.evaluate(() => ({
    route: location.hash,
    firstCost: document.querySelectorAll('tr.row')[0].cells[6].textContent,
    lastCost: [...document.querySelectorAll('tr.row')].pop().cells[6].textContent }));
  // the selected trial's panel names its pair's advisory verdict
  await page.goto(BASE + '/#/exp/exp-p/trials?sel=trial-b1', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  out.panel = await page.evaluate(() => (document.querySelector('.panel') || {}).textContent || '');
"""
        out = drive(base, body, tmp_path)
        assert any(h.startswith("cost (") for h in out["headers"])  # currency named
        assert "wall (s)" in out["headers"]
        assert "sort=-cost" in out["sorted"]["route"]
        assert out["sorted"]["firstCost"] == "0.9"
        assert out["sorted"]["lastCost"] == "—"  # unmeasured sorts last
        assert "judge on this pair: B (ADVISORY)" in out["panel"]
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- trial detail: telemetry + advisory verdict + forensics three-state ------------
def test_trial_detail_surfaces_telemetry_verdict_and_forensics_state(tmp_path):
    fx = rich_experiment(tmp_path / "exp-a")
    _paired_fixture(tmp_path / "exp-p")  # no forensics scan on this ledger
    clean = fx["trial_ids"][("t2", "control")]  # scanned, covered, no flags
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-a/trial/""" + clean + """', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.head = await page.evaluate(() => document.querySelector('#app .toolbar').textContent);
  await page.goto(BASE + '/#/exp/exp-a/trial/""" + clean + """?tab=forensics', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  out.covered = await page.evaluate(() => document.getElementById('app').textContent.includes('covered by the latest scan'));
  await page.goto(BASE + '/#/exp/exp-p/trial/trial-a1?tab=forensics', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.noScan = await page.evaluate(() => document.getElementById('app').textContent.includes('no forensics scan on this ledger yet'));
  await page.goto(BASE + '/#/exp/exp-p/trial/trial-a1?tab=trajectory', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  out.telemetry = await page.evaluate(() => document.querySelector('#app .toolbar').textContent);
"""
        out = drive(base, body, tmp_path)
        assert "cost" in out["head"] and "tokens in" in out["head"]
        # the covered-but-clean trial is NOT described as never-scanned
        assert out["covered"] is True
        assert out["noScan"] is True
        # measured telemetry renders; the pair's advisory verdict is named
        assert "cost 0.9" in out["telemetry"]
        assert "judge on this pair (ADVISORY): B" in out["telemetry"]
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- compare: an opened flight recorder must survive the poll re-render ------------
def test_compare_flight_recorder_stays_open_across_polls(tmp_path):
    """The open/closed state of a flight-recorder panel is VIEW state: it must
    live in the URL like every other view knob (panel/facets/slice), so the
    1.5s poll re-render cannot swallow the operator's click, a shared link
    reproduces the open panel [AC-3], and closing round-trips the same way."""
    from tests.test_eval14_page_drive import _reasoning_experiment

    _reasoning_experiment(tmp_path / "exp-r")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-r/compare', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  const frState = () => ({
    open: !!document.querySelector('details.fr[open]'),
    frParam: new URLSearchParams(location.hash.split('?')[1] || '').get('fr') });
  out.before = await page.evaluate(frState);
  await page.click('details.fr summary');
  await page.waitForTimeout(400);
  out.opened = await page.evaluate(frState);
  // outlive several poll re-renders (POLL_MS = 1500)
  await page.waitForTimeout(4000);
  out.afterPolls = await page.evaluate(() => !!document.querySelector('details.fr[open]'));
  // a shared link reproduces the open panel
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  out.reloaded = await page.evaluate(() => !!document.querySelector('details.fr[open]'));
  // closing is the same URL round-trip, and stays closed past a poll
  await page.click('details.fr summary');
  await page.waitForTimeout(2500);
  out.closed = await page.evaluate(frState);
"""
        out = drive(base, body, tmp_path)
        assert out["before"] == {"open": False, "frParam": None}
        assert out["opened"]["open"] is True and out["opened"]["frParam"]
        assert out["afterPolls"] is True   # the poll re-render must not close it
        assert out["reloaded"] is True     # the URL reproduces the view
        assert out["closed"] == {"open": False, "frParam": None}
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- compare: reasoning entries show measured usage; absence stays absent ----------
def test_compare_reasoning_entries_show_measured_usage(tmp_path):
    """A reasoning entry's measured tokens/cost render beside its content, so a
    post-reveal human can tell a metered model turn from an unmeasured one (the
    reference agent's orchestrator, a v1 recorder, ...). Null renders as NOTHING
    — unmeasured is never dressed as zero, and the page never claims
    'code-authored' (that would be an inference the data cannot prove)."""
    from tests.test_eval14_page_drive import _reasoning_experiment

    _reasoning_experiment(tmp_path / "exp-r")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-r/compare?fr=cmp-t1-r0', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2200);
  out.meta = await page.evaluate(() => ({
    metas: [...document.querySelectorAll('.rz .rmeta')].map(e => e.textContent),
    reasons: document.querySelectorAll('.rz .reason').length }));
"""
        out = drive(base, body, tmp_path)
        # 2 arms x 2 entries render; ONLY the measured entry (x2 arms) gets a
        # usage line, and it carries both figures
        assert out["meta"]["reasons"] == 4
        assert len(out["meta"]["metas"]) == 2
        assert all("412 tok" in m and "0.0021" in m for m in out["meta"]["metas"])
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- compare: ungraded stays neutral; the pair index navigates ---------------------
def test_compare_ungraded_pair_neutral_and_index(tmp_path):
    _paired_fixture(tmp_path / "exp-p")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-p/compare', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  out.cmp = await page.evaluate(() => {
    const chips = [...document.querySelectorAll('.chip')];
    const ungraded = chips.filter(c => c.textContent.includes('holdout: ungraded'));
    const idxRows = [...document.querySelectorAll('.card table tr.row')];
    return {
      ungradedCount: ungraded.length,
      ungradedNeutral: ungraded.every(c => !c.className.includes('bad') && !c.className.includes('ok')),
      excludedNote: document.getElementById('app').textContent.includes('not fully graded, excluded'),
      judgeTallyNamed: chips.some(c => c.textContent.startsWith('B \\u00b7 treatment 1')),
      indexRows: idxRows.length,
      anchors: [...document.querySelectorAll('.card.pairjump')].map(c => c.id),
    };
  });
"""
        out = drive(base, body, tmp_path)
        # the t2 pair is ungraded on both sides: neutral chips, never red
        assert out["cmp"]["ungradedCount"] == 2
        assert out["cmp"]["ungradedNeutral"] is True
        # the banner names what its tallies exclude
        assert out["cmp"]["excludedNote"] is True
        # judge tallies name the arm, not a bare letter
        assert out["cmp"]["judgeTallyNamed"] is True
        # the index lists both pairs and each card carries its jump anchor
        assert out["cmp"]["indexRows"] == 2
        assert set(out["cmp"]["anchors"]) == {"pair-cmp-t1-r0", "pair-cmp-t2-r0"}
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)
