"""The operator dashboard page [EVAL-13 AC-6, D003].

One self-contained HTML document: inline CSS, inline script, relative
``fetch('/api/…')`` calls only — no external URI schemes, no fetched assets, no
href/src/link/@import/url() references (the dossier's self-containment
property, minus its script ban: this is a live tool, not an archival
artifact). All dynamic values land via ``textContent`` — ledger strings are
data, never markup.

The page is the **openly-unblinded operator tier** and says so on every render
[D003, the EVAL-9 openly-unblinded precedent]: arm identities are visible by
design, so a person who watches this view is disqualified from serving as this
experiment's EVAL-7 blinded reviewer. Staleness of a ``running`` heartbeat is
judged client-side (the harness never guesses at liveness it did not observe).
"""

from __future__ import annotations

OPERATOR_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>verdi-bench — operator view</title>
<style>
  :root {
    --surface-1: #fcfcfb; --plane: #f9f9f7;
    --ink-1: #0b0b0b; --ink-2: #52514e; --ink-3: #898781;
    --hairline: #e1e0d9; --border: rgba(11,11,11,0.10);
    --meter: #2a78d6;
    --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --surface-1: #1a1a19; --plane: #0d0d0d;
      --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-3: #898781;
      --hairline: #2c2c2a; --border: rgba(255,255,255,0.10);
      --meter: #3987e5;
      --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
    }
  }
  * { box-sizing: border-box; margin: 0; }
  body {
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--plane); color: var(--ink-1);
    padding: 20px; font-size: 14px; line-height: 1.45;
  }
  main { max-width: 1080px; margin: 0 auto; display: grid; gap: 12px; }
  .banner {
    background: var(--surface-1); border: 1px solid var(--border);
    border-left: 3px solid var(--warning); border-radius: 6px;
    padding: 10px 14px; color: var(--ink-2);
  }
  .banner strong { color: var(--ink-1); }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; }
  header h1 { font-size: 18px; font-weight: 650; }
  .chip {
    display: inline-block; padding: 1px 9px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--surface-1);
    color: var(--ink-2); font-size: 12px; white-space: nowrap;
  }
  .card {
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 6px; padding: 12px 14px;
  }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
  .tile .label { color: var(--ink-3); font-size: 12px; }
  .tile .value { font-size: 26px; font-weight: 650; margin: 2px 0 6px; }
  .tile .sub { color: var(--ink-2); font-size: 12px; }
  .meter { height: 6px; border-radius: 3px; background: var(--hairline); overflow: hidden; }
  .meter > div { height: 100%; border-radius: 3px; background: var(--meter); width: 0%; }
  h2 { font-size: 13px; font-weight: 650; color: var(--ink-2); margin-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th { text-align: left; color: var(--ink-3); font-size: 12px; font-weight: 500;
       border-bottom: 1px solid var(--hairline); padding: 4px 8px 6px 0; }
  td { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--hairline); }
  tr:last-child td { border-bottom: none; }
  #feed { list-style: none; font-variant-numeric: tabular-nums; }
  #feed li { display: flex; gap: 10px; padding: 4px 0; border-bottom: 1px solid var(--hairline); }
  #feed li:last-child { border-bottom: none; }
  #feed .ts { color: var(--ink-3); font-size: 12px; white-space: nowrap; }
  #feed .kind { color: var(--ink-2); font-size: 12px; min-width: 150px; }
  footer { color: var(--ink-3); font-size: 12px; }
  .ok { color: var(--good); } .bad { color: var(--critical); } .warn-ink { color: var(--ink-1); }
</style>
</head>
<body>
<main>
  <div class="banner">&#9888;&#65039; <strong>Unblinded operator view.</strong>
    Arm identities are visible by design. Anyone who watches this page is
    <strong>disqualified from serving as this experiment's blinded (EVAL-7)
    reviewer</strong> — their arm-recognition answers and the judge&#8211;human
    kappa would be silently corrupted. Read-only: this page appends nothing.</div>

  <header>
    <h1 id="exp">experiment</h1>
    <span class="chip" id="chain">chain: …</span>
    <span class="chip" id="hb">heartbeat: …</span>
    <span class="chip" id="conn">connecting…</span>
  </header>

  <section class="tiles">
    <div class="card tile">
      <div class="label">Cells (done / planned)</div>
      <div class="value" id="cells">–</div>
      <div class="meter"><div id="cells-meter"></div></div>
      <div class="sub" id="cells-sub"></div>
    </div>
    <div class="card tile">
      <div class="label">Spend vs ceiling</div>
      <div class="value" id="spend">–</div>
      <div class="meter"><div id="spend-meter"></div></div>
      <div class="sub" id="spend-sub"></div>
    </div>
    <div class="card tile">
      <div class="label">In flight</div>
      <div class="value" id="flight">–</div>
      <div class="sub" id="flight-sub"></div>
    </div>
    <div class="card tile">
      <div class="label">Pipeline</div>
      <div class="value" id="pipeline">–</div>
      <div class="sub" id="pipeline-sub"></div>
    </div>
  </section>

  <section class="card">
    <h2>Per-arm trials (unblinded)</h2>
    <table>
      <thead><tr><th>arm</th><th>trials</th><th>completed</th><th>timeout</th><th>infra failed</th></tr></thead>
      <tbody id="arms"></tbody>
    </table>
  </section>

  <section class="card">
    <h2>Ledger feed (newest first)</h2>
    <ul id="feed"></ul>
  </section>

  <footer>verdi-bench operator view — polls /api/status and /api/events on this
    server only; every value is derived from the hash-chained ledger and the
    operational heartbeat. Local records are ADVISORY.</footer>
</main>

<script>
"use strict";
const POLL_MS = 1500, FEED_MAX = 40, STALE_MS = 90000;
let cursor = 0;
const feed = [];
const $ = (id) => document.getElementById(id);

function fmt(n, digits) {
  return (n === null || n === undefined) ? "?" :
    Number(n).toLocaleString(undefined, { maximumFractionDigits: digits === undefined ? 2 : digits });
}

function setChip(el, text, cls) {
  el.textContent = text;
  el.classList.remove("ok", "bad");
  if (cls) el.classList.add(cls);
}

function renderStatus(s) {
  $("exp").textContent = "experiment " + s.experiment_id;
  const chain = s.chain || {};
  setChip($("chain"), chain.ok ? ("\\u2713 chain OK (" + fmt(chain.events, 0) + " events)")
                              : "\\u2715 chain BROKEN: " + (chain.detail || ""),
          chain.ok ? "ok" : "bad");

  const hb = s.heartbeat;
  if (!hb) { setChip($("hb"), "heartbeat: none"); }
  else {
    let label = "heartbeat: " + hb.state;
    let cls = hb.state === "finished" ? "ok" : null;
    const age = Date.now() - Date.parse(hb.ts);
    if (hb.state === "running" && isFinite(age) && age > STALE_MS) {
      label = "\\u26A0 heartbeat stale (" + Math.round(age / 1000) + "s, was running)";
      cls = null;
    }
    setChip($("hb"), label, cls);
  }

  const st = s.stages;
  if (!st) {
    $("cells").textContent = "withheld";
    $("cells-sub").textContent = chain.ok ? "" : "ledger content unverified — fail closed";
    return;
  }
  const cells = st.cells, spend = st.spend;
  $("cells").textContent = fmt(cells.done, 0) + " / " + fmt(cells.planned, 0);
  $("cells-meter").style.width =
    cells.planned ? Math.min(100, 100 * cells.done / cells.planned) + "%" : "0%";
  $("cells-sub").textContent = "infra failures: " + fmt(cells.infra_failures, 0);

  $("spend").textContent = fmt(spend.accumulated) + " / " + fmt(spend.ceiling);
  $("spend-meter").style.width =
    spend.ceiling ? Math.min(100, 100 * spend.accumulated / spend.ceiling) + "%" : "0%";
  $("spend-sub").textContent = (spend.currency || "") +
    (spend.stopped_cost_ceiling ? " — STOPPED at ceiling" : "");

  const flight = s.heartbeat && s.heartbeat.in_flight;
  if (flight) {
    $("flight").textContent = flight.task_id + " / " + flight.arm;
    const started = Date.parse(flight.started_ts);
    const elapsed = isFinite(started) ? Math.max(0, Math.round((Date.now() - started) / 1000)) + "s" : "";
    $("flight-sub").textContent =
      "rep " + flight.repetition + ", attempt " + flight.attempt + (elapsed ? ", " + elapsed + " elapsed" : "");
  } else {
    $("flight").textContent = "idle";
    $("flight-sub").textContent = "";
  }

  $("pipeline").textContent =
    fmt(st.grade.graded, 0) + " graded, " + fmt(st.judge.verdicts, 0) + " judged";
  $("pipeline-sub").textContent =
    "grade pending " + fmt(st.grade.pending, 0) +
    " · review " + fmt(st.review.human_verdicts, 0) + "/" + fmt(st.review.packets, 0) +
    " · selfcheck " + st.analyze.selfcheck +
    (st.quarantines.length ? " · quarantined " + st.quarantines.length : "");

  const tbody = $("arms");
  tbody.textContent = "";
  for (const [arm, c] of Object.entries(st.per_arm)) {
    const tr = document.createElement("tr");
    for (const v of [arm, c.trials, c.completed, c.timeout, c.infra_failed]) {
      const td = document.createElement("td");
      td.textContent = String(v);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function summarize(ev) {
  const kind = ev.event || "?";
  const rec = ev.trial_record || {};
  switch (kind) {
    case "trial":
      return rec.task_id + " / " + rec.arm + " rep" + rec.repetition + " \\u2192 " + rec.outcome;
    case "trial_infra_failed":
      return ev.task_id + " / " + ev.arm + " \\u2192 " + ev.reason;
    case "grade":
      return ev.trial_id + " \\u2192 " + (ev.binary_score ? "pass" : "fail");
    case "cant_grade":
      return ev.trial_id + " \\u2192 " + ev.reason;
    case "judge_verdict": {
      const v = ev.verdict || {};
      return (v.comparison_id || "") + " \\u2192 " + (v.winner || "");
    }
    case "run_stopped_cost_ceiling":
      return "spend " + fmt(ev.accumulated_cost) + " of " + fmt(ev.ceiling);
    case "experiment_locked":
      return "seed " + ev.seed + ", spec " + String(ev.spec_sha256 || "").slice(0, 12);
    default:
      return "";
  }
}

function renderFeed() {
  const ul = $("feed");
  ul.textContent = "";
  for (const ev of feed.slice(-FEED_MAX).reverse()) {
    const li = document.createElement("li");
    const ts = document.createElement("span"); ts.className = "ts";
    ts.textContent = ((ev.provenance || {}).ts || "").replace("T", " ").slice(0, 19);
    const kind = document.createElement("span"); kind.className = "kind";
    kind.textContent = ev.event || "?";
    const what = document.createElement("span");
    what.textContent = summarize(ev);
    li.append(ts, kind, what);
    ul.appendChild(li);
  }
}

async function poll() {
  try {
    const s = await (await fetch("/api/status")).json();
    renderStatus(s);
    const page = await (await fetch("/api/events?offset=" + cursor)).json();
    if (page.events) {
      cursor = page.next_offset;
      if (page.events.length) { feed.push(...page.events); renderFeed(); }
    }
    setChip($("conn"), "live \\u00b7 " + new Date().toLocaleTimeString(), "ok");
  } catch (e) {
    setChip($("conn"), "server unreachable \\u2014 retrying", "bad");
  } finally {
    setTimeout(poll, POLL_MS);
  }
}
poll();
</script>
</body>
</html>
"""
