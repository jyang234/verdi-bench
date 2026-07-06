"""EVAL-19 — operator UI P2: bundle export, filter grammar, views, honest polish.

AC map: static bundle (AC-1), typed grammar ↔ chips (AC-2), saved views
(AC-3), null-honest ETA + cost sparklines (AC-4), tallies as navigation
(AC-5), posture under growth (AC-6). Reuses EVAL-14's ``rich_experiment``
fixture and its headless drive helper. Spec: docs/design/specs/eval19.spec.md.
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
from harness.ledger.query import read_events
from harness.serve.bundle import write_bundle
from harness.serve.server import make_server
from tests.fixtures.browser import drive
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade
from tests.fixtures.scenarios import rich_experiment

runner = CliRunner()
_REPO = Path(__file__).resolve().parents[1]
_NEEDLES = ("http://", "https://", "src=", "href=", "url(", "@import", "<link")


def _serve_root(root):
    srv = make_server(None, root=root, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


def _stop(srv, thread):
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _digest(d: Path) -> list:
    return sorted(
        (str(p.relative_to(d)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in d.rglob("*") if p.is_file()
    )


# --- AC-1: static bundle export -------------------------------------------------
def test_ac1_bundle_deterministic_selfcontained(tmp_path):
    fx = rich_experiment(tmp_path / "exp-a")
    expdir = tmp_path / "exp-a"
    before = _digest(expdir)
    events_before = len(read_events(fx["ledger"]))

    out = tmp_path / "out"
    out.mkdir()
    b1 = write_bundle(expdir, out / "one.html")
    b2 = write_bundle(expdir, out / "two.html")
    assert b1.read_bytes() == b2.read_bytes()  # byte-deterministic [AC-1]

    # bundling is a pure read: no event, no mutation, and the CLI flag is the
    # same writer (D001) — its output is byte-identical too
    r = runner.invoke(app, ["serve", str(expdir), "--bundle", str(out / "cli.html")])
    assert r.exit_code == 0, r.output
    assert "no server started" in r.output
    assert (out / "cli.html").read_bytes() == b1.read_bytes()
    r = runner.invoke(app, ["serve", "--root", str(tmp_path), "--bundle", str(out / "nope.html")])
    assert r.exit_code == 2 and "one experiment" in r.output
    assert _digest(expdir) == before
    assert len(read_events(fx["ledger"])) == events_before

    # self-contained: the dossier's needle property holds over the archive,
    # embedded ledger data included (escapes, not links)
    text = b1.read_text(encoding="utf-8")
    for needle in _NEEDLES:
        assert needle not in text, f"external/active reference {needle!r} in bundle"
    assert "const BUNDLE = null;" not in text  # the seam was replaced with data

    # it opens from the filesystem: every screen renders with the banner and
    # the archive says what it is — no server, no fetch, no console errors
    fileurl = b1.as_uri()
    body = """
  await page.goto(FILEURL + '#/exp/exp-a', { waitUntil: 'load' });
  await page.waitForTimeout(900);
  out.live = await page.evaluate(() => ({
    bundle: window.__vb().bundle,
    banner: document.body.textContent.includes('Unblinded operator view'),
    archived: document.body.textContent.includes('Static bundle'),
    chip: document.body.textContent.includes('STATIC BUNDLE'),
    advisory: document.body.textContent.includes('ADVISORY'),
    events: window.__vb().events }));
  await page.goto(FILEURL + '#/exp/exp-a/trials', { waitUntil: 'load' });
  await page.waitForTimeout(700);
  out.trials = await page.evaluate(() => window.__vb().rows);
  await page.goto(FILEURL + '#/exp/exp-a/trial/' + FLAGGED + '?tab=forensics', { waitUntil: 'load' });
  await page.waitForTimeout(700);
  out.trial = await page.evaluate(() => document.body.textContent.includes('suspicious_single_step'));
  await page.goto(FILEURL + '#/exp/exp-a/compare', { waitUntil: 'load' });
  await page.waitForTimeout(700);
  out.compare = await page.evaluate(() => ({
    cards: document.querySelectorAll('.diff2').length,
    exploratory: document.body.textContent.includes('EXPLORATORY') }));
  await page.goto(FILEURL + '#/exp/exp-a/findings', { waitUntil: 'load' });
  await page.waitForTimeout(700);
  out.findings = await page.evaluate(() => ({
    fence: document.querySelectorAll('.fence li').length,
    honest: document.body.textContent.includes('not embedded in a bundle') }));
""".replace("FILEURL", json.dumps(fileurl)).replace("FLAGGED", json.dumps(fx["flagged"]))
    res = drive("unused://", body, tmp_path)
    assert res["live"] == {"bundle": True, "banner": True, "archived": True,
                           "chip": True, "advisory": True, "events": res["live"]["events"]}
    assert res["live"]["events"] > 0
    assert res["trials"] == 4
    assert res["trial"] is True
    assert res["compare"] == {"cards": 2, "exploratory": True}
    assert res["findings"] == {"fence": 9, "honest": True}  # +insulation [F-M-C3]
    assert res["__errors"] == []


# --- AC-2: the typed grammar and the chips are one state ---------------------------
def test_ac2_grammar_chips_one_state(tmp_path):
    rich_experiment(tmp_path / "exp-a")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-a/trials', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  const type = async (text) => {
    await page.fill('#gram', text);
    await page.press('#gram', 'Enter');
    await page.waitForTimeout(500);
    return page.evaluate(() => ({ route: location.hash, rows: window.__vb().rows,
      gram: window.__vb().grammar, err: window.__vb().filterErr,
      chips: [...document.querySelectorAll('.chip.on')].map(c => c.textContent) }));
  };
  // grammar term → URL state; the chip renders the same grammar back
  out.negated = await type('arm:control -graded:pass');
  // the SAME state made by chip clicks: clear, then click the offered chips
  out.cleared = await type('');
  await page.evaluate(() => { [...document.querySelectorAll('.chip.click')].find(c => c.textContent === 'graded: fail').click(); });
  await page.waitForTimeout(500);
  out.viaChip = await page.evaluate(() => ({ route: location.hash, rows: window.__vb().rows,
    gram: window.__vb().grammar }));
  out.viaGrammar = await type('graded:fail');
  // wildcards on id-like fields; free text; both round-trip through the input
  out.wild = await type('task:t1*');
  out.freeText = await type('t2');
  // malformed input: a named error in place, the previous filter intact
  out.beforeErr = await type('arm:treatment');
  out.unknown = await type('bogus:1');
  out.dup = await type('arm:control arm:treatment');
  out.badWild = await type('arm:con*');
  out.negWord = await type('-freeword');
"""
        out = drive(base, body, tmp_path)
        # negation: one URL state, chips mirror the grammar text
        assert out["negated"]["route"].endswith("?arm=control&graded=-pass")
        assert out["negated"]["rows"] == 1  # control ∩ not-pass = the failed t1 control
        assert out["negated"]["gram"] == "arm:control -graded:pass"
        assert "-graded: pass ✕" in out["negated"]["chips"]
        assert "arm: control ✕" in out["negated"]["chips"]
        # chip clicks and grammar text produce the identical URL state and rows
        assert out["cleared"]["route"].endswith("/trials")
        assert out["viaChip"]["route"] == out["viaGrammar"]["route"]
        assert out["viaChip"]["rows"] == out["viaGrammar"]["rows"] == 1
        assert out["viaChip"]["gram"] == "graded:fail"  # the input renders the state
        # wildcard + free text round-trip
        assert out["wild"]["rows"] == 2 and out["wild"]["err"] is None
        assert out["wild"]["gram"] == "task:t1*"  # the input renders the canonical form
        assert "task=t1*" in out["wild"]["route"].replace("%2A", "*")
        assert out["freeText"]["rows"] == 2 and out["freeText"]["gram"] == "t2"
        # every malformed input names its error and leaves the filter untouched
        prev = out["beforeErr"]
        for key, fragment in [("unknown", "unknown field 'bogus'"),
                              ("dup", "given twice"),
                              ("badWild", "id-like fields only"),
                              ("negWord", "not free text")]:
            assert fragment in (out[key]["err"] or ""), (key, out[key])
            assert out[key]["route"] == prev["route"], key  # never a partial filter
            assert out[key]["rows"] == prev["rows"], key
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- AC-3: saved views — local, URL-canonical -----------------------------------------
def test_ac3_saved_views_local_url_canonical(tmp_path):
    rich_experiment(tmp_path / "exp-a")
    expdir = tmp_path / "exp-a"
    before = _digest(expdir)
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-a/trials?arm=control&graded=-pass', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  await page.fill('#viewname', 'mine');
  await page.click('#viewsave');
  await page.waitForTimeout(400);
  out.saved = await page.evaluate(() => ({ views: window.__vb().views, rows: window.__vb().rows }));
  // duplicate names are refused with the reason, not silently overwritten
  await page.fill('#viewname', 'mine');
  await page.click('#viewsave');
  await page.waitForTimeout(400);
  out.dup = await page.evaluate(() => ({ views: window.__vb().views, err: window.__vb().viewsErr }));
  // navigate away, restore by clicking the view: the exact URL and rows come back
  await page.goto(BASE + '/#/exp/exp-a/trials', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  out.blank = await page.evaluate(() => window.__vb().rows);
  await page.evaluate(() => { [...document.querySelectorAll('.chip.click')].find(c => c.textContent === 'view: mine').click(); });
  await page.waitForTimeout(600);
  out.restored = await page.evaluate(() => ({ route: location.hash, rows: window.__vb().rows }));
  // the store survives a reload (localStorage), and the URL stays canonical
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.reloaded = await page.evaluate(() => ({ views: window.__vb().views, route: location.hash,
    caption: document.body.textContent.includes('a saved view is a stored URL') }));
  await page.fill('#viewname', 'ours');
  await page.evaluate(() => { [...document.querySelectorAll('button.btn')].find(b => b.textContent === '\\u270e').click(); });
  await page.waitForTimeout(400);
  out.renamed = await page.evaluate(() => window.__vb().views);
  await page.evaluate(() => { [...document.querySelectorAll('button.btn')].find(b => b.textContent === '\\u2715').click(); });
  await page.waitForTimeout(400);
  out.deleted = await page.evaluate(() => window.__vb().views);
"""
        out = drive(base, body, tmp_path)
        assert out["saved"]["views"] == ["mine"] and out["saved"]["rows"] == 1
        assert out["dup"]["views"] == ["mine"] and "exists" in out["dup"]["err"]
        assert out["blank"] == 4  # navigation really left the filtered slice
        assert out["restored"]["route"].endswith("?arm=control&graded=-pass")
        assert out["restored"]["rows"] == 1  # the stored URL reproduces the slice
        assert out["reloaded"]["views"] == ["mine"] and out["reloaded"]["caption"] is True
        assert out["renamed"] == ["ours"]
        assert out["deleted"] == []
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)
    # views never move trust server-side: the experiment directory is untouched
    assert _digest(expdir) == before


# --- AC-4: honest small multiples ----------------------------------------------------
def _partial_run_experiment(dirpath: Path, *, n_per_arm: int, repetitions: int = 2):
    """A locked experiment mid-run: ``n_per_arm`` seeded trials per arm against
    a plan of ``2 tasks × 2 arms × repetitions`` cells. Control carries costs
    [0.25, None, 0.5, …] (a null mid-series = a gap), treatment costs are all
    null (unmeasured, never zero)."""
    locked_experiment(dirpath, repetitions=repetitions)
    (dirpath / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}, {"id": "t2", "prompt": "p"}]}),
        encoding="utf-8",
    )
    ctx = fixed_ctx(experiment_id=dirpath.name)
    control_costs = [0.25, None, 0.5, None]
    cells = [("t1", 0), ("t1", 1), ("t2", 0), ("t2", 1)][:n_per_arm]
    for i, (task, rep) in enumerate(cells):
        seed_trial_and_grade(
            dirpath / "ledger.ndjson", ctx, trial_id=f"c-{task}-r{rep}", task_id=task,
            arm="control", repetition=rep, passed=True,
            telemetry={"cost": control_costs[i]} if control_costs[i] is not None else None,
        )
        seed_trial_and_grade(
            dirpath / "ledger.ndjson", ctx, trial_id=f"x-{task}-r{rep}", task_id=task,
            arm="treatment", repetition=rep, passed=True,
        )
    return dirpath


def test_ac4_eta_sparklines_null_honest(tmp_path):
    # 6 of 8 cells done, 6 completion timestamps → a labeled approximation;
    # 2 of 8 done → below the minimum sample, the estimate is ABSENT
    _partial_run_experiment(tmp_path / "exp-mid", n_per_arm=3)
    _partial_run_experiment(tmp_path / "exp-two", n_per_arm=1)
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-mid', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.mid = await page.evaluate(() => ({ eta: window.__vb().eta, spark: window.__vb().spark,
    text: document.body.textContent.includes('approximate, from 6 completion timestamps'),
    paths: [...document.querySelectorAll('.tile svg path')].map(p => p.getAttribute('d')),
    noCosts: document.body.textContent.includes('no measured costs'),
    gaps: document.body.textContent.includes('unmeasured (gaps)') }));
  await page.goto(BASE + '/#/exp/exp-two', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  // rendered tiles only: body.textContent would also match the page's own
  // inline script source, which legitimately contains the label template
  out.two = await page.evaluate(() => ({ eta: window.__vb().eta,
    absent: ![...document.querySelectorAll('.tile .sub')].some(el => el.textContent.includes('approximate, from')) }));
"""
        out = drive(base, body, tmp_path)
        # ETA: remaining 2 cells at the observed pace (trial events 2s apart on
        # the fixture clock) → 4s, labeled approximate, sample disclosed
        assert out["mid"]["eta"] == {"seconds": 4, "sample": 6, "remaining": 2}
        assert out["mid"]["text"] is True
        # sparkline points are the cumulative measured costs; the null-cost
        # trial contributes NO point and the line restarts after the gap
        assert out["mid"]["spark"]["control"] == {"ys": [0.25, 0.75], "nulls": 1}
        assert out["mid"]["spark"]["treatment"] == {"ys": [], "nulls": 3}
        assert len(out["mid"]["paths"]) == 1  # only the measured arm draws a line
        d = out["mid"]["paths"][0]
        assert len(d.split("M")) - 1 == 2, d  # the gap breaks the path
        assert out["mid"]["noCosts"] is True and out["mid"]["gaps"] is True
        # below the minimum sample: no estimate anywhere — absent, not zero
        assert out["two"]["eta"] is None and out["two"]["absent"] is True
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- AC-5: tallies are navigation ------------------------------------------------------
def test_ac5_tallies_navigate(tmp_path):
    fx = rich_experiment(tmp_path / "exp-a")
    srv, thread, base = _serve_root(tmp_path)
    try:
        body = """
  await page.goto(BASE + '/#/exp/exp-a/compare', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  out.all = await page.evaluate(() => document.querySelectorAll('.diff2').length);
  // the arm tally filters to exactly the pairs it counts, state in the URL
  await page.evaluate(() => { [...document.querySelectorAll('.chip.click')].find(c => c.textContent.startsWith('treatment 1')).click(); });
  await page.waitForTimeout(600);
  out.slice = await page.evaluate(() => ({ route: location.hash,
    cards: document.querySelectorAll('.diff2').length,
    task: (document.querySelector('.card .toolbar b') || {}).textContent || '' }));
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  out.reloaded = await page.evaluate(() => ({ route: location.hash,
    cards: document.querySelectorAll('.diff2').length }));
  // a judge tally slices the advisory tier; re-clicking clears the slice
  await page.evaluate(() => { [...document.querySelectorAll('.chip.click')].find(c => c.textContent.startsWith('unjudged 1')).click(); });
  await page.waitForTimeout(600);
  out.unjudged = await page.evaluate(() => ({ route: location.hash,
    cards: document.querySelectorAll('.diff2').length }));
  await page.evaluate(() => { [...document.querySelectorAll('.chip.on')].find(c => c.textContent.startsWith('unjudged 1')).click(); });
  await page.waitForTimeout(600);
  out.cleared = await page.evaluate(() => ({ route: location.hash,
    cards: document.querySelectorAll('.diff2').length }));
  // a forensic flag chip deep-links to that trial's forensics tab
  await page.goto(BASE + '/#/exp/exp-a/trials', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.evaluate(() => { [...document.querySelectorAll('.chip.bad.click')].find(c => c.textContent.includes('flag')).click(); });
  await page.waitForTimeout(700);
  out.flag = await page.evaluate(() => ({ route: location.hash,
    forensicsOn: !!([...document.querySelectorAll('.chip.on')].find(c => c.textContent === 'forensics')),
    detector: document.body.textContent.includes('suspicious_single_step') }));
"""
        out = drive(base, body, tmp_path)
        assert out["all"] == 2
        assert "slice=holdout%3Ab_only" in out["slice"]["route"]
        assert out["slice"]["cards"] == 1 and out["slice"]["task"].startswith("t1")
        assert out["reloaded"]["route"] == out["slice"]["route"]  # shareable slice
        assert out["reloaded"]["cards"] == 1
        assert "slice=judge%3Aunjudged" in out["unjudged"]["route"]
        assert out["unjudged"]["cards"] == 1  # the unjudged pair is t2
        assert "slice=" not in out["cleared"]["route"] and out["cleared"]["cards"] == 2
        assert out["flag"]["route"] == "#/exp/exp-a/trial/" + fx["flagged"] + "?tab=forensics"
        assert out["flag"]["forensicsOn"] is True and out["flag"]["detector"] is True
        assert out["__errors"] == []
    finally:
        _stop(srv, thread)


# --- AC-6: posture under growth, again ---------------------------------------------
def test_ac6_posture_unchanged(tmp_path):
    from harness.serve.page import OPERATOR_PAGE

    # the live page keeps its needle property AND its bundle seam (exactly one:
    # write_bundle refuses an ambiguous replacement)
    for needle in _NEEDLES:
        assert needle not in OPERATOR_PAGE
    assert OPERATOR_PAGE.count("const BUNDLE = null;") == 1

    # the bundle writer lives inside harness.serve, so the observability
    # contracts cover it by package; prove that coverage is load-bearing with
    # a planted judge-client import (the XC-5 plant pattern)
    text = (_REPO / ".importlinter").read_text(encoding="utf-8")
    obs = text.split("[importlinter:contract:observability-llm-free]", 1)[1]
    assert "harness.serve" in obs
    ledger_contract = text.split("[importlinter:contract:ledger-writes-only-via-events]", 1)[1] \
                          .split("[importlinter:contract:", 1)[0]
    assert "harness.serve" in ledger_contract
    from tests.test_import_contracts import _run_lint

    module = _REPO / "harness" / "serve" / "bundle.py"
    original = module.read_text(encoding="utf-8")
    planted = (
        original
        + "\n\ndef _planted_contract_violation():  # test-injected, restored below\n"
        + "    import harness.judge.client  # noqa\n"
    )
    try:
        module.write_text(planted, encoding="utf-8")
        result = _run_lint()
        assert result.returncode != 0, "planted judge-client import broke no contract"
        assert "observability-llm-free" in result.stdout or "Read-only observability" in result.stdout
    finally:
        module.write_text(original, encoding="utf-8")

    # no new event kinds, no new entrypoints, and the server gained no route:
    # bundling is a CLI-side pure read, never an endpoint
    import harness.serve.bundle  # noqa: F401
    from harness.entrypoints import all_entrypoints
    from harness.ledger.events import REGISTERED_EVENTS

    assert not {k for k in REGISTERED_EVENTS if "bundle" in k or "saved_view" in k}
    assert not {e.name for e in all_entrypoints() if "bundle" in e.name}

    rich_experiment(tmp_path / "exp-a")
    srv, thread, base = _serve_root(tmp_path)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base + "/api/bundle")
        assert excinfo.value.code == 404
        req = urllib.request.Request(base + "/api/experiments", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req)
        assert excinfo.value.code == 405  # still a GET-only observer
    finally:
        _stop(srv, thread)
