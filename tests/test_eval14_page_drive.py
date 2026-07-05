"""EVAL-14 — operator page headless drives.

AC map here: hash-route round-trips (AC-3), facets/panel/keyboard (AC-4),
feed tail ergonomics (AC-5). Drives the real served page in the pre-installed
Chromium via the node playwright stack; skips honestly when the environment
lacks it (the docker-marker precedent). Server-side ACs live in
``test_eval14_observability_ui.py``. Spec: docs/design/specs/eval14.spec.md.
"""

from __future__ import annotations

import threading

from harness.ledger import events as ledger_events
from harness.serve.server import make_server
from tests.fixtures.browser import drive
from tests.test_eval14_observability_ui import rich_experiment


def _serve_root(root):
    srv = make_server(None, root=root, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


# --- AC-3: deep links round-trip ------------------------------------------------
def test_ac3_hash_routes_round_trip(tmp_path):
    fx = rich_experiment(tmp_path / "exp-a")
    flagged = fx["flagged"]
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  // each route loads directly (a shared link), renders its screen, keeps its state
  await page.goto(BASE + '/#/experiments', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.home = await page.evaluate(() => ({ route: window.__vb().route, rows: window.__vb().rows }));

  await page.goto(BASE + '/#/exp/exp-a/trials?arm=control', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.filtered = await page.evaluate(() => ({ route: window.__vb().route, rows: window.__vb().rows }));

  // clicking a facet chip rewrites the URL (state lives in the URL)
  await page.evaluate(() => {
    const chip = [...document.querySelectorAll('.chip.click')].find(c => c.textContent.startsWith('graded: fail'));
    chip.click();
  });
  await page.waitForTimeout(600);
  out.afterChip = await page.evaluate(() => ({ route: window.__vb().route, rows: window.__vb().rows }));

  // reload the rewritten URL: the same slice comes back
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.reloaded = await page.evaluate(() => ({ route: window.__vb().route, rows: window.__vb().rows }));

  await page.goto(BASE + '/#/exp/exp-a/trial/""" + flagged + """?tab=grade', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.trial = await page.evaluate(() => ({
    route: window.__vb().route,
    head: (document.querySelector('#app .toolbar b') || {}).textContent || '',
    gradeTabOn: !!([...document.querySelectorAll('.chip.on')].find(c => c.textContent === 'grade')),
  }));

  await page.goto(BASE + '/#/exp/exp-a/compare?only=disagreements', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  out.compare = await page.evaluate(() => ({
    route: window.__vb().route,
    exploratory: document.body.textContent.includes('EXPLORATORY'),
    pairCards: document.querySelectorAll('.diff2').length,
  }));

  await page.goto(BASE + '/#/exp/exp-a/findings', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.findings = await page.evaluate(() => ({ items: document.querySelectorAll('.fence li').length }));
"""
        out = drive(base, body, tmp_path)
        assert out["home"]["route"] == "#/experiments" and out["home"]["rows"] == 1
        assert out["filtered"]["route"] == "#/exp/exp-a/trials?arm=control"
        assert out["filtered"]["rows"] == 2  # two control trials
        assert "graded=fail" in out["afterChip"]["route"]
        assert out["afterChip"]["rows"] == 1  # control ∩ fail = the t1 control trial
        assert out["reloaded"]["route"] == out["afterChip"]["route"]
        assert out["reloaded"]["rows"] == 1  # reload restores the exact slice
        assert out["trial"]["head"] == flagged and out["trial"]["gradeTabOn"] is True
        assert out["compare"]["exploratory"] is True
        assert out["compare"]["pairCards"] == 1  # only the disagreement pair renders
        assert out["findings"]["items"] == 9  # +insulation [F-M-C3]
        assert out["__errors"] == []
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=5)


# --- AC-4: facets, side panel, keyboard --------------------------------------------
def test_ac4_facets_panel_keyboard(tmp_path):
    rich_experiment(tmp_path / "exp-a")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-a/trials', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.all = await page.evaluate(() => window.__vb().rows);

  // j selects row 1 → the selection is a URL param (deep-linkable panel, D002)
  await page.keyboard.press('j');
  await page.waitForTimeout(500);
  out.afterJ = await page.evaluate(() => ({
    route: window.__vb().route, panel: !!document.querySelector('.panel') }));
  // j again advances; k comes back
  await page.keyboard.press('j');
  await page.waitForTimeout(500);
  const selAfterJJ = await page.evaluate(() => new URLSearchParams(location.hash.split('?')[1]).get('sel'));
  await page.keyboard.press('k');
  await page.waitForTimeout(500);
  const selAfterK = await page.evaluate(() => new URLSearchParams(location.hash.split('?')[1]).get('sel'));
  out.movement = { selAfterJJ, selAfterK, distinct: selAfterJJ !== selAfterK };

  // esc closes the panel; enter on a selection opens the full page
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  out.escClosed = await page.evaluate(() => ({
    panel: !!document.querySelector('.panel'),
    sel: new URLSearchParams(location.hash.split('?')[1] || '').get('sel') }));
  await page.keyboard.press('j');
  await page.waitForTimeout(400);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(800);
  out.entered = await page.evaluate(() => window.__vb().route);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  out.escBack = await page.evaluate(() => window.__vb().route);

  // clicking a row opens the panel without losing the table
  await page.evaluate(() => { document.querySelectorAll('tr.row')[2].click(); });
  await page.waitForTimeout(500);
  out.clicked = await page.evaluate(() => ({
    panel: !!document.querySelector('.panel'),
    tableRows: document.querySelectorAll('tr.row').length }));
"""
        out = drive(base, body, tmp_path)
        assert out["all"] == 4
        assert "sel=trial-" in out["afterJ"]["route"] and out["afterJ"]["panel"] is True
        assert out["movement"]["distinct"] is True
        assert out["escClosed"] == {"panel": False, "sel": None}
        assert out["entered"].startswith("#/exp/exp-a/trial/trial-")
        assert out["escBack"].startswith("#/exp/exp-a/trials")
        assert out["clicked"]["panel"] is True and out["clicked"]["tableRows"] == 4
        assert out["__errors"] == []
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=5)


# --- AC-5: feed tail ergonomics -----------------------------------------------------
def test_ac5_feed_tail_ergonomics(tmp_path):
    fx = rich_experiment(tmp_path / "exp-a")
    srv, thread, base = _serve_root(tmp_path)

    def append_events(n: int) -> None:
        for i in range(n):
            ledger_events.record_cant_grade(
                fx["ledger"], fx["ctx"], trial_id=f"tail-fixture-{i}",
                reason="grader_unavailable",
            )

    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-a', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);
  const s1 = await page.evaluate(() => window.__vb());
  out.start = { cursor: s1.cursor, events: s1.events };

  // python appends 3 events mid-window; the page tails them from its cursor
  await page.waitForTimeout(6000);
  const s2 = await page.evaluate(() => window.__vb());
  out.afterAppend = { cursor: s2.cursor, events: s2.events };

  // hover pauses: the DOM freezes while events keep accruing in the buffer
  await page.hover('#feedbox');
  await page.waitForTimeout(300);
  const frozenRows = await page.evaluate(() => document.querySelectorAll('#feedbox li').length);
  await page.waitForTimeout(10000);
  out.paused = await page.evaluate(() => ({
    paused: window.__vb().paused,
    newCount: window.__vb().newCount,
    rows: document.querySelectorAll('#feedbox li').length }));
  out.frozenRows = frozenRows;

  // leaving the feed resumes and clears the buffer count
  await page.mouse.move(10, 10);
  await page.waitForTimeout(2500);
  out.resumed = await page.evaluate(() => ({
    paused: window.__vb().paused, newCount: window.__vb().newCount,
    rows: document.querySelectorAll('#feedbox li').length }));
"""
        # the page can't call back into python, so appends fire from timers
        # while the page polls: append 1 lands inside the first 6s tail window
        # (after the s1 snapshot), append 2 inside the 7s hover window — each
        # with multi-second margins against goto/poll jitter.
        appender = threading.Timer(7.5, append_events, args=(3,))
        appender2 = threading.Timer(17.0, append_events, args=(2,))
        appender.start()
        appender2.start()
        out = drive(base, body, tmp_path)
        appender.join()
        appender2.join()
        assert out["start"]["cursor"] > 0 and out["start"]["events"] > 0
        # tail: exactly the appended events arrived, cursor advanced, no re-read
        assert out["afterAppend"]["events"] == out["start"]["events"] + 3
        assert out["afterAppend"]["cursor"] > out["start"]["cursor"]
        # pause: viewport frozen while events keep accruing in the buffer
        assert out["paused"]["paused"] is True
        assert out["paused"]["rows"] == out["frozenRows"]
        assert out["paused"]["newCount"] == 2
        # resume: buffer cleared, feed catches up
        assert out["resumed"]["paused"] is False and out["resumed"]["newCount"] == 0
        assert out["resumed"]["rows"] >= out["frozenRows"] + 2
        assert out["__errors"] == []
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=5)


def _reasoning_experiment(exp_dir):
    """A 2-arm generic experiment whose native log carries agent-attributed
    reasoning — for driving the compare screen's flight-recorder panel [EVAL-24]."""
    from pathlib import Path

    import yaml

    from harness.judge.assemble import comparison_id_for
    from harness.ledger.query import read_events
    from harness.plan.interleave import derive_schedule, enumerate_trials
    from harness.run.engines.fake import FakeEngine
    from harness.run.interleave import schedule
    from harness.run.types import RunConfig, Task
    from tests.fixtures.builders import fixed_ctx, locked_experiment

    arms_cfg = [
        {"name": "control", "platform": "generic",
         "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
        {"name": "treatment", "platform": "generic",
         "model": "openai/gpt-4.1-mini-2025-04-14", "payload": {}},
    ]
    spec, _sp, ledger = locked_experiment(exp_dir, arms=arms_cfg, repetitions=1)
    (exp_dir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8")
    ctx = fixed_ctx(experiment_id=exp_dir.name)
    arms = {a.name: a for a in spec.arms}
    native = {"verdi_log_version": 1, "telemetry": {"tokens_out": 40},
              "trajectory": [{"kind": "file_edit", "files_touched": ["solution.py"], "agent": "worker-1"}],
              "reasoning": [
                  {"content": "plan: decompose into add, then verify", "agent": "planner"},
                  {"content": "add(a, b) returns a + b; handled overflow", "agent": "worker-1"}]}
    tasks = {"t1": Task(id="t1", prompt="p", fake_behavior={"native_log": native})}
    order = derive_schedule(spec.seed, enumerate_trials(["t1"], list(arms), 1))
    schedule(order, tasks=tasks, arms=arms, workspace_root=exp_dir / "workspaces",
             ledger_path=ledger, ctx=ctx, config=RunConfig(engine=FakeEngine()),
             cost_ceiling=spec.cost_ceiling.amount)
    trial_ids = {}
    for ev in read_events(ledger):
        if ev.get("event") == "trial":
            rec = ev["trial_record"]
            trial_ids[rec["arm"]] = rec["trial_id"]
            ws = Path(rec["artifacts_path"]).parent
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "solution.py").write_text(f"# {rec['arm']}\n", encoding="utf-8")
    for arm, passed in (("control", False), ("treatment", True)):
        ledger_events.record_grade(
            ledger, ctx, trial_id=trial_ids[arm], task_sha="s",
            assertions=[{"id": "h1", "source": "holdout_test",
                         "result": "pass" if passed else "fail"}],
            binary_score=passed)
    ledger_events.append_verdict(ledger, ctx, verdict={
        "comparison_id": comparison_id_for("t1", 0), "winner": "B", "reason": "x",
        "provenance": {"judge_model": "google/gemini-1.5-pro-002", "rubric_sha256": "s"}})
    return exp_dir


def test_compare_renders_flight_recorder_reasoning_by_role(tmp_path):
    """The compare screen renders per-arm reasoning grouped by sub-agent role —
    the EVAL-24 flight recorder, operator-tier. Skips honest without the browser
    stack (the docker-marker precedent); the compare-payload data itself is
    covered locally by test_ac5/test_ac6 in test_eval24_flightrec.py."""
    _reasoning_experiment(tmp_path / "exp-r")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-r/compare', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  out.rz = await page.evaluate(() => ({
    panels: document.querySelectorAll('.rz').length,
    roles: [...document.querySelectorAll('.rz .role')].map(e => e.textContent),
    hasContent: document.body.textContent.includes('decompose into add'),
    armheads: [...document.querySelectorAll('.armhead')].map(e => e.textContent),
  }));
"""
        out = drive(base, body, tmp_path)
        assert out["rz"]["panels"] >= 1                 # the reasoning panel renders
        assert "planner" in out["rz"]["roles"]          # grouped by sub-agent role
        assert "worker-1" in out["rz"]["roles"]
        assert out["rz"]["hasContent"] is True          # reasoning content shown
        # the columns are labeled with the arms (A · control / B · treatment)
        assert any("control" in a and "treatment" in a for a in out["rz"]["armheads"])
        assert out["__errors"] == []
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=5)
