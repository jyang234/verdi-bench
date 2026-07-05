"""The operator dashboard page [EVAL-13 AC-6; EVAL-14 AC-3..AC-5; EVAL-19 AC-2..AC-5].

One self-contained HTML document (D001: dependency-free single-file app):
inline CSS, inline script, relative ``fetch('/api/…')`` calls only — no
external URI schemes, no fetched assets, no href/src/link/@import/url()
references. Navigation out (the dossier artifact) uses scripted
``window.open`` on relative paths, never an anchor, so the needle property
holds verbatim. All dynamic values land via ``textContent`` — ledger strings
are data, never markup. Inline SVG (the cost sparklines) is created through
the namespace of a static ``<svg>`` prototype in the document, so no
namespace URI string ever appears in the page bytes.

Structure: a hash router over six screens (workspace home, experiment live,
trials, trial detail, compare, findings). Every view state that matters —
screen, experiment, trial, facets, the open side panel (D002), the filter
grammar, the compare slice — lives in the URL, so a link reproduces the
exact slice [AC-3]. The typed filter grammar and the facet chips are two
projections of that one URL state [EVAL-19 AC-2]; saved views are stored
URL fragments in localStorage, never on the server [EVAL-19 AC-3/D002].
List screens share j/k/enter/esc conventions [AC-4]; the ledger feed rides
the byte cursor with follow/pause/new-pill ergonomics and never re-reads
from offset zero in its poll loop [AC-5]. ``window.__vb()`` exposes a small
read-only state snapshot as an explicit test seam for the headless AC
drives — it is not an API.

``const BUNDLE = null;`` is the static-export seam [EVAL-19 AC-1]:
``harness.serve.bundle.write_bundle`` replaces that one line with the
archived data object, turning the same document into a no-server snapshot —
the data helper short-circuits to the embedded object and the poll loop
never re-arms.

The page is the **openly-unblinded operator tier** and says so on every
render [EVAL-13 D003]. Staleness of a ``running`` heartbeat is judged
client-side (the harness never guesses at liveness it did not observe).
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
    --meter: #2a78d6; --meter2: #7d4fc9; --soft: #eef3fa;
    --good: #0ca30c; --warning: #9a6b00; --critical: #d03b3b;
    --add: rgba(12,163,12,0.14); --del: rgba(208,59,59,0.13);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --surface-1: #1a1a19; --plane: #0d0d0d;
      --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-3: #898781;
      --hairline: #2c2c2a; --border: rgba(255,255,255,0.10);
      --meter: #3987e5; --meter2: #a98ae8; --soft: #1c2733;
      --good: #0ca30c; --warning: #d9a21b; --critical: #e05252;
      --add: rgba(12,163,12,0.18); --del: rgba(224,82,82,0.16);
    }
  }
  * { box-sizing: border-box; margin: 0; }
  body {
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--plane); color: var(--ink-1);
    padding: 18px; font-size: 14px; line-height: 1.45;
  }
  main { max-width: 1180px; margin: 0 auto; display: grid; gap: 12px; }
  .banner {
    background: var(--surface-1); border: 1px solid var(--border);
    border-left: 3px solid var(--warning); border-radius: 6px;
    padding: 9px 14px; color: var(--ink-2); font-size: 13px;
  }
  .banner strong { color: var(--ink-1); }
  header.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-height: 30px; }
  header.bar .crumb { font-size: 16px; font-weight: 650; }
  header.bar .crumb .up { color: var(--ink-3); font-weight: 400; cursor: pointer; }
  header.bar .crumb .up:hover { color: var(--ink-1); text-decoration: underline; }
  .chip {
    display: inline-flex; align-items: center; gap: 5px; padding: 1px 9px;
    border-radius: 999px; border: 1px solid var(--border); background: var(--surface-1);
    color: var(--ink-2); font-size: 12px; white-space: nowrap;
  }
  .chip.ok { color: var(--good); } .chip.bad { color: var(--critical); }
  .chip.click { cursor: pointer; } .chip.click:hover { border-color: var(--ink-3); }
  .chip.on { background: var(--soft); color: var(--ink-1); border-color: var(--meter); }
  .spacer { flex: 1; }
  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 12px; }
  .tile .label { color: var(--ink-3); font-size: 12px; }
  .tile .value { font-size: 24px; font-weight: 650; margin: 2px 0 6px; }
  .tile .sub { color: var(--ink-2); font-size: 12px; }
  .meter { height: 6px; border-radius: 3px; background: var(--hairline); overflow: hidden; }
  .meter > div { height: 100%; border-radius: 3px; background: var(--meter); width: 0%; }
  h2 { font-size: 13px; font-weight: 650; color: var(--ink-2); margin-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th { text-align: left; color: var(--ink-3); font-size: 11.5px; font-weight: 500;
       border-bottom: 1px solid var(--hairline); padding: 4px 8px 6px 0; white-space: nowrap; }
  td { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--hairline); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr.row { cursor: pointer; }
  tr.row:hover td { background: var(--soft); }
  tr.sel td { background: var(--soft); }
  .mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; }
  .dim { color: var(--ink-2); } .dim3 { color: var(--ink-3); }
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .btn {
    font-size: 12px; color: var(--ink-2); border: 1px solid var(--border);
    border-radius: 6px; padding: 3px 11px; background: var(--surface-1); cursor: pointer;
    font-family: inherit;
  }
  .btn:hover { border-color: var(--ink-3); color: var(--ink-1); }
  .btn:focus-visible, .chip.click:focus-visible { outline: 2px solid var(--meter); outline-offset: 1px; }
  input.search {
    font: inherit; font-size: 12.5px; color: var(--ink-1); background: var(--surface-1);
    border: 1px solid var(--border); border-radius: 6px; padding: 3px 10px; min-width: 190px;
  }
  .split { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr); gap: 12px; }
  .panel { background: var(--surface-1); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; align-self: start; position: sticky; top: 12px; }
  #feedbox li { display: flex; gap: 10px; padding: 4px 0; border-bottom: 1px solid var(--hairline); font-size: 12.5px; }
  #feedbox li:last-child { border-bottom: none; }
  #feedbox { list-style: none; max-height: 420px; overflow-y: auto; }
  #feedbox .ts { color: var(--ink-3); font-size: 11.5px; white-space: nowrap; font-family: ui-monospace, Menlo, Consolas, monospace; }
  #feedbox .k { color: var(--ink-2); min-width: 140px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; }
  .pill { font-size: 11.5px; color: var(--surface-1); background: var(--meter); border: none;
          border-radius: 999px; padding: 2px 10px; font-weight: 600; cursor: pointer; }
  .rail { display: flex; gap: 6px; flex-wrap: wrap; }
  .stage { flex: 1; min-width: 108px; border: 1px solid var(--border); border-radius: 7px; padding: 7px 10px; background: var(--surface-1); }
  .stage .nm { font-size: 10.5px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--ink-3); font-weight: 600; }
  .stage .st { font-size: 12.5px; font-weight: 600; margin-top: 2px; }
  .steps { list-style: none; }
  .steps li { display: flex; gap: 10px; align-items: baseline; padding: 5px 0; border-bottom: 1px solid var(--hairline); font-size: 12.5px; }
  .steps li:last-child { border-bottom: none; }
  .steps .ico { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; color: var(--ink-3); width: 72px; flex: none; }
  .steps .t { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; color: var(--ink-3); width: 48px; flex: none; text-align: right; }
  .diff2 { display: grid; grid-template-columns: 1fr 1fr; border: 1px solid var(--hairline); border-radius: 6px; overflow: hidden; }
  .diff2 > div { padding: 8px 12px; min-width: 0; }
  .diff2 > div:first-child { border-right: 1px solid var(--hairline); }
  .code { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 11.5px;
          line-height: 1.55; color: var(--ink-2); overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  .code .add { background: var(--add); color: var(--ink-1); }
  .code .del { background: var(--del); color: var(--ink-1); }
  .rz .role { font-size: 10px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--ink-3); font-weight: 600; margin: 8px 0 3px; }
  .rz .role:first-child { margin-top: 0; }
  .rz .reason { font-size: 12px; line-height: 1.5; color: var(--ink-2); white-space: pre-wrap; word-break: break-word; margin-bottom: 5px; }
  .rz .none { color: var(--ink-3); font-size: 12px; }
  .armhead { display: grid; grid-template-columns: 1fr 1fr; margin: 2px 0 4px; }
  .armhead > div { font-size: 11px; font-weight: 600; color: var(--ink-3); text-transform: uppercase; letter-spacing: 0.04em; padding: 0 2px; }
  .kbdrow { color: var(--ink-3); font-size: 12px; }
  kbd { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; border: 1px solid var(--hairline);
        border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; background: var(--surface-1); color: var(--ink-2); }
  .empty { color: var(--ink-3); text-align: center; padding: 28px 0; }
  .fence li { display: flex; gap: 8px; padding: 4px 0; font-size: 13px; align-items: baseline; }
  .fence { list-style: none; }
  .gerr { color: var(--critical); font-size: 12px; }
  .sparkrow { display: flex; align-items: center; gap: 6px; }
  .sparkrow svg { flex: none; }
  .gram { min-width: 300px; }
  @media (max-width: 860px) { .split, .diff2 { grid-template-columns: 1fr; }
    .diff2 > div:first-child { border-right: none; border-bottom: 1px solid var(--hairline); } }
</style>
</head>
<body>
<main>
  <div class="banner">&#9888;&#65039; <strong>Unblinded operator view.</strong>
    Arm identities are visible by design; anyone who watches this page is
    <strong>disqualified from serving as these experiments' blinded (EVAL-7)
    reviewers</strong>. Read-only: this page appends nothing.<span id="bundlenote"></span></div>
  <header class="bar" id="bar"></header>
  <div id="app"></div>
  <svg id="svgp" width="0" height="0" aria-hidden="true"></svg>
  <div class="kbdrow"><kbd>j</kbd>/<kbd>k</kbd> select &#183; <kbd>enter</kbd> open &#183;
    <kbd>esc</kbd> back/close &#183; state lives in the URL &#8212; share it</div>
</main>

<script>
"use strict";
/* static-export seam [EVAL-19 AC-1]: write_bundle replaces this one line with
   the archived data object; everything below treats BUNDLE as the no-server
   data source and stops polling. In the live page it stays null. */
const BUNDLE = null;
/* ------------------------------------------------------------------ state */
const POLL_MS = 1500, FEED_MAX = 400, ETA_MIN_SAMPLE = 3;
const VIEWS_KEY = "vb.views.v1";
const FILTER_FIELDS = ["arm", "task", "outcome", "graded", "flagged"];
const WILDCARD_FIELDS = ["task"];
const SPARK_COLORS = ["var(--meter)", "var(--meter2)"];
const S = {
  experiments: null,          // /api/experiments rows
  exp: {},                    // per-experiment: {events, cursor, status, sel...}
  route: null,
  sel: 0,                     // selected row index on the current list screen
  paused: false, newCount: 0, // feed ergonomics
  timer: null,
};
function expState(name) {
  if (!S.exp[name]) S.exp[name] = { events: [], cursor: 0, status: null,
                                    compare: null, fence: null, trial: {} };
  return S.exp[name];
}
/* explicit read-only test seam for the headless AC drives — not an API */
window.__vb = () => {
  const r = S.route || {};
  const st = r.exp ? expState(r.exp) : null;
  return {
    route: location.hash, sel: S.sel, paused: S.paused, newCount: S.newCount,
    cursor: st ? st.cursor : 0,
    events: st ? st.events.length : 0,
    rows: (document.querySelectorAll("tr.row") || []).length,
    bundle: !!BUNDLE,
    views: loadViews().map(v => v.name),
    filterErr: S.filterErr || null, viewsErr: S.viewsErr || null,
    grammar: r.screen === "trials" ? serializeFilter(r.params) : null,
    eta: st ? etaFromEvents(st) : null,
    spark: st ? sparkSummary(st) : null,
  };
};

/* ------------------------------------------------------------------ router */
function parseHash() {
  const h = (location.hash || "#/experiments").slice(1);
  const [path, qs] = h.split("?");
  const seg = path.split("/").filter(Boolean);
  const params = new URLSearchParams(qs || "");
  if (seg[0] === "exp" && seg[1]) {
    const exp = decodeURIComponent(seg[1]);
    if (seg[2] === "trials") return { screen: "trials", exp, params };
    if (seg[2] === "trial" && seg[3]) return { screen: "trial", exp, id: decodeURIComponent(seg[3]), params };
    if (seg[2] === "compare") return { screen: "compare", exp, params };
    if (seg[2] === "findings") return { screen: "findings", exp, params };
    return { screen: "exp", exp, params };
  }
  return { screen: "home", params };
}
function nav(hash) { location.hash = hash; }
function setParam(key, value) {
  const p = S.route.params;
  if (value === null || value === "" || value === undefined) p.delete(key); else p.set(key, value);
  const qs = p.toString();
  const base = location.hash.split("?")[0];
  location.hash = qs ? base + "?" + qs : base;
}

/* ------------------------------------------------------------------ data */
/* One lookup for the whole page: the live observer fetches; a static bundle
   answers from its embedded object — same shapes, no transport [EVAL-19 AC-1]. */
function bundleData(url) {
  const halves = url.split("?");
  const path = halves[0], p = new URLSearchParams(halves[1] || "");
  if (path === "/api/experiments") return { experiments: BUNDLE.experiments };
  const exp = p.get("exp");
  if (exp !== BUNDLE.experiment)
    throw new Error("experiment '" + exp + "' is not in this bundle (it archives '" + BUNDLE.experiment + "')");
  if (path === "/api/status") return BUNDLE.status;
  if (path === "/api/events") {
    const off = Number(p.get("offset") || "0");
    if (off >= BUNDLE.next_offset) return { events: [], next_offset: BUNDLE.next_offset };
    return { events: BUNDLE.events, next_offset: BUNDLE.next_offset };
  }
  if (path === "/api/timeline") return BUNDLE.timeline;
  if (path === "/api/trial") {
    const d = BUNDLE.trials[p.get("id") || ""];
    if (!d) throw new Error("trial '" + p.get("id") + "' is not in this bundle");
    return d;
  }
  if (path === "/api/compare") return BUNDLE.compare;
  if (path === "/api/fence") return BUNDLE.fence;
  throw new Error("route '" + path + "' is not in this bundle");
}
async function j(url) { if (BUNDLE) return bundleData(url); const r = await fetch(url); if (!r.ok) throw new Error((await r.json()).error || r.status); return r.json(); }
async function refresh() {
  const r = S.route;
  try {
    if (r.screen === "home" || S.experiments === null) {
      S.experiments = (await j("/api/experiments")).experiments;
    }
    if (r.exp) {
      const st = expState(r.exp);
      st.status = await j("/api/status?exp=" + encodeURIComponent(r.exp));
      const page = await j("/api/events?exp=" + encodeURIComponent(r.exp) + "&offset=" + st.cursor);
      if (page.events.length) {
        st.events.push(...page.events);
        if (S.paused || feedScrolled()) S.newCount += page.events.length;
      }
      st.cursor = page.next_offset;
      if (r.screen === "compare" && !st.compare) st.compare = await j("/api/compare?exp=" + encodeURIComponent(r.exp));
      if (r.screen === "findings") st.fence = await j("/api/fence?exp=" + encodeURIComponent(r.exp));
      if (r.screen === "trial" && !st.trial[r.id])
        st.trial[r.id] = await j("/api/trial?exp=" + encodeURIComponent(r.exp) + "&id=" + encodeURIComponent(r.id));
    }
    S.online = true;
  } catch (e) { S.online = false; S.lastError = String(e && e.message || e); }
  if (!(S.paused && S.route.screen === "exp")) render();
  /* a bundle's data cannot change: render per navigation, never poll */
  if (!BUNDLE) { clearTimeout(S.timer); S.timer = setTimeout(refresh, POLL_MS); }
}

/* ------------------------------------------------------------------ dom */
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids) if (kid !== null && kid !== undefined) el.append(kid);
  return el;
}
const fmt = (n, d) => (n === null || n === undefined) ? "?" :
  Number(n).toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 2 : d });
const nm = (v) => (v === null || v === undefined) ? "not measured" : String(v);

/* --------------------------------------------------------- derived trials */
function deriveTrials(st) {
  const grades = {}, cants = {}, quarantined = {}, flags = {};
  let latestReport = null;
  for (const ev of st.events) {
    if (ev.event === "grade") grades[ev.trial_id] = ev;
    else if (ev.event === "cant_grade") cants[ev.trial_id] = ev;
    else if (ev.event === "forensic_quarantine") quarantined[(ev.forensic_quarantine || {}).trial_id] = true;
    else if (ev.event === "forensics_report") latestReport = ev.forensics_report;
  }
  for (const f of ((latestReport || {}).flags || [])) (flags[f.trial_id] = flags[f.trial_id] || []).push(f);
  return st.events.filter(e => e.event === "trial").map(e => {
    const r = e.trial_record;
    const g = grades[r.trial_id], c = cants[r.trial_id];
    return {
      trial_id: r.trial_id, task_id: r.task_id, arm: r.arm, repetition: r.repetition,
      outcome: r.outcome, cost: (r.telemetry || {}).cost, wall: (r.telemetry || {}).wall_time_s,
      graded: g ? (g.binary_score ? "pass" : "fail") : (c ? "cant_grade" : "pending"),
      flagged: !!(flags[r.trial_id] || []).length, quarantined: !!quarantined[r.trial_id],
      egress: ((r.flags || {}).egress_attempts || []).length,
    };
  });
}
/* ------------------------------------------- filter grammar [EVAL-19 AC-2]
   A CLOSED grammar over the facet params; grammar text and chips are two
   projections of the same URLSearchParams state. Anything outside the
   productions is a named parse error — never a guessed partial filter.
   Negation is a leading "-" on a field term and rides INSIDE the param value,
   so the URL stays the single canonical state (a literal leading "-" in a
   facet value is not expressible in v1 — documented in the ? card). */
function parseFilter(text) {
  const parsed = { fields: {}, q: [] };
  for (const tok of (text || "").split(" ").map(s => s.trim()).filter(Boolean)) {
    let t = tok, neg = false;
    if (t[0] === "-") {
      neg = true; t = t.slice(1);
      if (!t) throw new Error("bare '-' \\u2014 negation is -field:value");
    }
    const i = t.indexOf(":");
    if (i === 0) throw new Error("missing field name before ':' in '" + tok + "'");
    if (i > 0) {
      const field = t.slice(0, i), value = t.slice(i + 1);
      if (FILTER_FIELDS.indexOf(field) < 0)
        throw new Error("unknown field '" + field + "' \\u2014 fields: " + FILTER_FIELDS.join(", "));
      if (!value) throw new Error("empty value in '" + tok + "'");
      if (value.indexOf("*") >= 0 && WILDCARD_FIELDS.indexOf(field) < 0)
        throw new Error("'*' wildcards apply to id-like fields only (" + WILDCARD_FIELDS.join(", ") + ")");
      if (field in parsed.fields)
        throw new Error("field '" + field + "' given twice \\u2014 one term per field");
      parsed.fields[field] = (neg ? "-" : "") + value;
    } else {
      if (neg) throw new Error("negation applies to field:value terms, not free text");
      parsed.q.push(t);
    }
  }
  return parsed;
}
function serializeFilter(p) {
  const parts = [];
  for (const f of FILTER_FIELDS) {
    const raw = p.get(f);
    if (raw) parts.push(raw[0] === "-" ? "-" + f + ":" + raw.slice(1) : f + ":" + raw);
  }
  const q = (p.get("q") || "").trim();
  if (q) parts.push(q);
  return parts.join(" ");
}
function setFacetParams(parsed) {
  const p = S.route.params;
  for (const f of FILTER_FIELDS)
    if (parsed.fields[f] !== undefined) p.set(f, parsed.fields[f]); else p.delete(f);
  if (parsed.q.length) p.set("q", parsed.q.join(" ")); else p.delete("q");
  const qs = p.toString();
  const base = location.hash.split("?")[0];
  const next = qs ? base + "?" + qs : base;
  if (location.hash === next) render(); else location.hash = next;
}
function wildMatch(pattern, value) {
  const parts = pattern.split("*");
  if (parts.length === 1) return value === pattern;
  if (parts[0] && !value.startsWith(parts[0])) return false;
  let pos = parts[0].length;
  for (let k = 1; k < parts.length - 1; k++) {
    if (!parts[k]) continue;
    const at = value.indexOf(parts[k], pos);
    if (at < 0) return false;
    pos = at + parts[k].length;
  }
  const last = parts[parts.length - 1];
  if (!last) return true;
  return value.endsWith(last) && value.length - last.length >= pos;
}
function facetActual(t, key) {
  if (key === "task") return t.task_id;
  if (key === "flagged") return String(t.flagged);
  return String(t[key]);
}
function facetHit(t, key, raw) {
  const neg = raw[0] === "-";
  const v = neg ? raw.slice(1) : raw;
  const actual = facetActual(t, key);
  const hit = (key === "task" && v.indexOf("*") >= 0) ? wildMatch(v, actual) : actual === v;
  return neg ? !hit : hit;
}
function applyFacets(rows, p) {
  const words = (p.get("q") || "").toLowerCase().split(" ").filter(Boolean);
  return rows.filter(t => {
    for (const key of FILTER_FIELDS) {
      const raw = p.get(key);
      if (raw && !facetHit(t, key, raw)) return false;
    }
    for (const w of words) {
      const tid = t.trial_id.toLowerCase(), kid = t.task_id.toLowerCase();
      const hit = w.indexOf("*") >= 0 ? (wildMatch(w, tid) || wildMatch(w, kid))
                                      : (tid.indexOf(w) >= 0 || kid.indexOf(w) >= 0);
      if (!hit) return false;
    }
    return true;
  });
}

/* --------------------------------------------- saved views [EVAL-19 AC-3]
   A view IS a stored URL fragment: localStorage holds {name, hash} pairs,
   the URL stays the canonical shareable form, the server never sees them. */
function loadViews() {
  const raw = localStorage.getItem(VIEWS_KEY);
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v : [];
  } catch (e) {
    S.viewsErr = "saved views unreadable \\u2014 they reset on the next save";
    return [];
  }
}
function persistViews(views) { localStorage.setItem(VIEWS_KEY, JSON.stringify(views)); render(); }
function saveView(name) {
  const views = loadViews();
  const n = (name || "").trim() || ("view-" + (views.length + 1));
  if (views.some(v => v.name === n)) {
    S.viewsErr = "a view named '" + n + "' exists \\u2014 rename or delete it first"; render(); return;
  }
  S.viewsErr = null;
  views.push({ name: n, hash: location.hash });
  persistViews(views);
}
function renameView(oldName) {
  const el = document.getElementById("viewname");
  const n = ((el && el.value) || "").trim();
  const views = loadViews();
  if (!n) { S.viewsErr = "type the new name, then \\u270e"; render(); return; }
  if (views.some(v => v.name === n)) { S.viewsErr = "a view named '" + n + "' exists"; render(); return; }
  const v = views.find(x => x.name === oldName);
  if (v) { v.name = n; S.viewsErr = null; persistViews(views); }
}
function deleteView(name) { S.viewsErr = null; persistViews(loadViews().filter(v => v.name !== name)); }

/* ---------------------------------- honest small multiples [EVAL-19 AC-4] */
function etaFromEvents(st) {
  const stg = st.status && st.status.stages;
  if (!stg) return null;
  const remaining = stg.cells.planned - stg.cells.done;
  const ts = st.events.filter(e => e.event === "trial")
    .map(e => Date.parse((e.provenance || {}).ts))
    .filter(t => isFinite(t)).sort((a, b) => a - b);
  /* below the minimum sample (or with nothing left) there is no estimate —
     absent, never zero or a dash dressed as data */
  if (remaining <= 0 || ts.length < ETA_MIN_SAMPLE) return null;
  const span = ts[ts.length - 1] - ts[0];
  if (span <= 0) return null;
  const perCell = span / (ts.length - 1);
  return { seconds: Math.round(remaining * perCell / 1000), sample: ts.length, remaining };
}
function fmtDur(s) {
  if (s >= 3600) return Math.floor(s / 3600) + "h " + Math.round((s % 3600) / 60) + "m";
  if (s >= 60) return Math.floor(s / 60) + "m " + Math.round(s % 60) + "s";
  return Math.round(s) + "s";
}
function sparkSeries(st) {
  const series = {};
  for (const ev of st.events) {
    if (ev.event !== "trial") continue;
    const rec = ev.trial_record || {};
    const s = series[rec.arm] = series[rec.arm] || { pts: [], nulls: 0, i: 0, cum: 0 };
    const c = (rec.telemetry || {}).cost;
    /* a null cost is a GAP: the x slot advances, no point lands, the line
       breaks — unmeasured is never drawn as zero */
    if (c === null || c === undefined) { s.nulls += 1; s.i += 1; continue; }
    s.cum += c;
    s.pts.push({ x: s.i, y: s.cum });
    s.i += 1;
  }
  return series;
}
function sparkSummary(st) {
  const out = {}, series = sparkSeries(st);
  for (const arm of Object.keys(series))
    out[arm] = { ys: series[arm].pts.map(p => p.y), nulls: series[arm].nulls };
  return out;
}
/* SVG namespace via the static prototype element — the URI never appears in
   the page bytes, keeping the needle property honest */
function svgEl(tag, attrs) {
  const el = document.createElementNS(document.getElementById("svgp").namespaceURI, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  return el;
}
function sparkline(pts, xmax, color) {
  const W = 120, H = 26, PAD = 3;
  const ymax = Math.max.apply(null, pts.map(p => p.y)) || 1;
  const X = x => PAD + (xmax <= 0 ? 0 : (W - 2 * PAD) * x / xmax);
  const Y = y => H - PAD - (H - 2 * PAD) * y / ymax;
  const svg = svgEl("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H,
                             role: "img", "aria-label": "cumulative cost" });
  let d = "";
  pts.forEach((p, i) => {
    const restart = i === 0 || p.x - pts[i - 1].x > 1;  /* a gap breaks the line */
    d += (restart ? " M" : " L") + X(p.x).toFixed(1) + " " + Y(p.y).toFixed(1);
  });
  svg.append(svgEl("path", { d: d.trim(), fill: "none", stroke: color,
                             "stroke-width": "2", "stroke-linecap": "round" }));
  const last = pts[pts.length - 1];
  if (last) svg.append(svgEl("circle", { cx: X(last.x).toFixed(1), cy: Y(last.y).toFixed(1),
                                         r: "2.5", fill: color }));
  return svg;
}

/* ------------------------------------------------------------------ header */
function renderBar() {
  const bar = document.getElementById("bar");
  bar.textContent = "";
  const r = S.route, st = r.exp ? expState(r.exp) : null;
  const crumb = h("div", { class: "crumb" });
  if (r.screen === "home") crumb.append("experiments");
  else {
    const up = h("span", { class: "up", text: "experiments / ", onclick: () => nav("#/experiments") });
    crumb.append(up, h("span", { text: r.exp }));
    if (r.screen !== "exp") crumb.append(h("span", { class: "up", text: " / " + (r.screen === "trial" ? "trial" : r.screen) }));
  }
  bar.append(crumb);
  if (st && st.status) {
    const chain = st.status.chain || {};
    bar.append(h("span", { class: "chip " + (chain.ok ? "ok" : "bad"),
      text: chain.ok ? ("\\u2713 chain OK \\u00b7 " + fmt(chain.events, 0) + " events") : "\\u2715 chain BROKEN" }));
    bar.append(h("span", { class: "chip", text: "ADVISORY" }));
    const hb = st.status.heartbeat;
    if (hb) {
      let label = "heartbeat: " + hb.state, cls = "chip" + (hb.state === "finished" ? " ok" : "");
      const age = Date.now() - Date.parse(hb.ts);
      if (hb.state === "running" && isFinite(age) && age > 90000)
        { label = "\\u26a0 heartbeat stale " + Math.round(age / 1000) + "s"; cls = "chip"; }
      bar.append(h("span", { class: cls, text: label }));
    }
  }
  if (r.exp) {
    bar.append(h("span", { class: "spacer" }));
    for (const [label, screen] of [["live", ""], ["trials", "/trials"], ["compare", "/compare"], ["findings", "/findings"]]) {
      const on = (screen === "" && r.screen === "exp") || screen.slice(1) === r.screen;
      bar.append(h("span", { class: "chip click" + (on ? " on" : ""), tabindex: "0", text: label,
        onclick: () => nav("#/exp/" + encodeURIComponent(r.exp) + screen) }));
    }
  }
  bar.append(h("span", { class: r.exp ? "" : "spacer" }));
  if (BUNDLE) bar.append(h("span", { class: "chip" + (S.online === false ? " bad" : ""),
    text: "STATIC BUNDLE" + (S.online === false ? " \\u00b7 " + (S.lastError || "")
                                                : " \\u00b7 archived snapshot, does not update") }));
  else bar.append(h("span", { class: "chip " + (S.online ? "ok" : "bad"),
    text: S.online ? "live" : ("unreachable: " + (S.lastError || "")) }));
}

/* ------------------------------------------------------------------ screens */
function renderHome(app) {
  const rows = S.experiments || [];
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Experiments" }));
  if (!rows.length) { card.append(h("div", { class: "empty", text: "No experiment directories (with a ledger.ndjson) found under this root." })); app.append(card); return; }
  const table = h("table");
  table.append(h("tr", {}, ...["experiment", "state", "cells", "", "spend", "graded", "judged", "selfcheck", "updated"].map(t => h("th", { text: t }))));
  rows.forEach((e, i) => {
    const sm = e.summary;
    const tr = h("tr", { class: "row" + (i === S.sel ? " sel" : "") });
    tr.addEventListener("click", () => nav("#/exp/" + encodeURIComponent(e.name)));
    tr.append(h("td", {}, h("b", { text: e.name })));
    const state = e.chain && e.chain.ok === false ? "\\u2715 chain broken"
      : (e.heartbeat_state || (sm && sm.cells.done ? "idle" : "unplanned"));
    tr.append(h("td", {}, h("span", { class: "chip" + (e.heartbeat_state === "running" ? " ok" : (e.chain && e.chain.ok === false ? " bad" : "")), text: state })));
    if (!sm) {
      tr.append(h("td", { class: "dim3", text: "withheld" }), h("td"), h("td", { class: "dim3", text: "withheld" }),
                h("td", { class: "dim3", text: "\\u2014" }), h("td", { class: "dim3", text: "\\u2014" }),
                h("td", { class: "dim3", text: "\\u2014" }), h("td", { class: "dim3", text: "\\u2014" }));
    } else {
      tr.append(h("td", { class: "mono", text: fmt(sm.cells.done, 0) + "/" + fmt(sm.cells.planned, 0) }));
      const meter = h("div", { class: "meter", style: "width:76px" }, h("div"));
      if (sm.cells.planned) meter.firstChild.style.width = Math.min(100, 100 * sm.cells.done / sm.cells.planned) + "%";
      tr.append(h("td", {}, meter));
      tr.append(h("td", { class: "mono", text: fmt(sm.spend.accumulated) + "/" + fmt(sm.spend.ceiling) }));
      tr.append(h("td", { class: "mono", text: fmt(sm.grade.graded, 0) }));
      tr.append(h("td", { class: "mono", text: fmt(sm.judge.verdicts, 0) + "/" + fmt(sm.judge.pairs_expected, 0) }));
      tr.append(h("td", {}, h("span", { class: "chip", text: sm.selfcheck })));
      tr.append(h("td", { class: "dim3", text: (sm.last_event_ts || "").replace("T", " ").slice(0, 19) || "\\u2014" }));
    }
    table.append(tr);
  });
  card.append(table);
  app.append(card);
}

function summarize(ev) {
  const rec = ev.trial_record || {};
  switch (ev.event) {
    case "trial": return rec.task_id + " / " + rec.arm + " rep" + rec.repetition + " \\u2192 " + rec.outcome;
    case "trial_infra_failed": return ev.task_id + " / " + ev.arm + " \\u2192 " + ev.reason;
    case "grade": return ev.trial_id + " \\u2192 " + (ev.binary_score ? "pass" : "fail");
    case "cant_grade": return ev.trial_id + " \\u2192 " + ev.reason;
    case "judge_verdict": { const v = ev.verdict || {}; return (v.comparison_id || "") + " \\u2192 " + (v.winner || ""); }
    case "run_stopped_cost_ceiling": return "spend " + fmt(ev.accumulated_cost) + " of " + fmt(ev.ceiling);
    case "experiment_locked": return "seed " + ev.seed + ", spec " + String(ev.spec_sha256 || "").slice(0, 12);
    default: return "";
  }
}
function feedScrolled() { const el = document.getElementById("feedbox"); return !!el && el.scrollTop > 8; }

function renderExp(app) {
  const st = expState(S.route.exp), snap = st.status;
  if (!snap) { app.append(h("div", { class: "empty", text: "loading\\u2026" })); return; }
  const stg = snap.stages;
  if (!stg) { app.append(h("div", { class: "card", text: "Ledger content withheld: " + ((snap.chain || {}).detail || "chain failed verification") + " [fail closed]" })); return; }

  const rail = h("div", { class: "rail" });
  const stages = [
    ["run", fmt(stg.cells.done, 0) + "/" + fmt(stg.cells.planned, 0) + (snap.heartbeat && snap.heartbeat.in_flight ? " \\u00b7 in flight" : "")],
    ["grade", fmt(stg.grade.graded, 0) + " \\u00b7 " + fmt(stg.grade.pending, 0) + " pending"],
    ["judge", fmt(stg.judge.verdicts, 0) + "/" + fmt(stg.judge.pairs_expected, 0) + " pairs"],
    ["review", fmt(stg.review.human_verdicts, 0) + "/" + fmt(stg.review.packets, 0)],
    ["forensics", fmt(stg.forensics.reports, 0) + " scan" + (stg.forensics.latest ? " \\u00b7 " + stg.forensics.latest.flags + " flags" : "")],
    ["analyze", "selfcheck " + stg.analyze.selfcheck],
  ];
  for (const [name, val] of stages)
    rail.append(h("div", { class: "stage" }, h("div", { class: "nm", text: name }), h("div", { class: "st", text: val })));
  app.append(rail);

  const tiles = h("div", { class: "tiles" });
  const flight = snap.heartbeat && snap.heartbeat.in_flight;
  const cellsTile = h("div", { class: "card tile" });
  cellsTile.append(h("div", { class: "label", text: "Cells (done / planned)" }),
    h("div", { class: "value", text: fmt(stg.cells.done, 0) + " / " + fmt(stg.cells.planned, 0) }));
  const m1 = h("div", { class: "meter" }, h("div"));
  if (stg.cells.planned) m1.firstChild.style.width = Math.min(100, 100 * stg.cells.done / stg.cells.planned) + "%";
  cellsTile.append(m1, h("div", { class: "sub", text: "infra failures: " + fmt(stg.cells.infra_failures, 0) }));
  const eta = etaFromEvents(st);
  if (eta) cellsTile.append(h("div", { class: "sub", text: "\\u2248 " + fmtDur(eta.seconds) +
    " left \\u00b7 approximate, from " + eta.sample + " completion timestamps" }));
  tiles.append(cellsTile);

  const spendTile = h("div", { class: "card tile" });
  spendTile.append(h("div", { class: "label", text: "Spend vs ceiling" }),
    h("div", { class: "value", text: fmt(stg.spend.accumulated) + " / " + fmt(stg.spend.ceiling) }));
  const m2 = h("div", { class: "meter" }, h("div"));
  if (stg.spend.ceiling) m2.firstChild.style.width = Math.min(100, 100 * stg.spend.accumulated / stg.spend.ceiling) + "%";
  const perArm = Object.entries(stg.per_arm).map(([a, c]) => a + " " + fmt(c.cost)).join(" \\u00b7 ");
  spendTile.append(m2, h("div", { class: "sub", text: (stg.spend.currency || "") + (perArm ? " \\u00b7 " + perArm : "") + (stg.spend.stopped_cost_ceiling ? " \\u00b7 STOPPED" : "") }));
  const series = sparkSeries(st);
  Object.keys(stg.per_arm).forEach((arm, idx) => {
    const s = series[arm] || { pts: [], nulls: 0, i: 0 };
    const row = h("div", { class: "sub sparkrow" });
    row.append(h("span", { class: "mono", text: arm }));
    if (s.pts.length) {
      row.append(sparkline(s.pts, Math.max(1, s.i - 1), SPARK_COLORS[idx] || "var(--ink-3)"));
      row.append(h("span", { class: "mono", text: fmt(s.pts[s.pts.length - 1].y) }));
    } else row.append(h("span", { class: "dim3", text: "no measured costs" }));
    if (s.nulls) row.append(h("span", { class: "dim3", text: s.nulls + " unmeasured (gaps)" }));
    spendTile.append(row);
  });
  tiles.append(spendTile);

  const flightTile = h("div", { class: "card tile" });
  flightTile.append(h("div", { class: "label", text: "In flight" }));
  if (flight) {
    const started = Date.parse(flight.started_ts);
    const elapsed = isFinite(started) ? Math.max(0, Math.round((Date.now() - started) / 1000)) + "s" : "";
    flightTile.append(h("div", { class: "value", text: flight.task_id + " / " + flight.arm }),
      h("div", { class: "sub", text: "rep " + flight.repetition + ", attempt " + flight.attempt + (elapsed ? ", " + elapsed : "") }));
  } else flightTile.append(h("div", { class: "value", text: "idle" }));
  tiles.append(flightTile);

  const armsTile = h("div", { class: "card tile" });
  armsTile.append(h("div", { class: "label", text: "Per-arm trials (unblinded)" }));
  for (const [arm, c] of Object.entries(stg.per_arm))
    armsTile.append(h("div", { class: "sub", text: arm + ": " + c.trials + " (\\u2713" + c.completed + " t/o " + c.timeout + " infra " + c.infra_failed + ")" }));
  if (stg.quarantines.length) armsTile.append(h("div", { class: "sub", text: "quarantined: " + stg.quarantines.length }));
  tiles.append(armsTile);
  app.append(tiles);

  const feed = h("div", { class: "card" });
  const bar = h("div", { class: "toolbar" });
  bar.append(h("h2", { text: "Ledger feed (newest first)", style: "margin:0" }));
  const kinds = ["trial", "grade", "judge_verdict", "cant_grade", "trial_infra_failed"];
  const active = S.route.params.get("kind") || "";
  bar.append(h("span", { class: "chip click" + (!active ? " on" : ""), tabindex: "0", text: "all", onclick: () => setParam("kind", null) }));
  for (const k of kinds)
    bar.append(h("span", { class: "chip click" + (active === k ? " on" : ""), tabindex: "0", text: k, onclick: () => setParam("kind", k) }));
  bar.append(h("span", { class: "spacer" }));
  if (S.newCount > 0)
    bar.append(h("button", { class: "pill", text: S.newCount + " new \\u2191", onclick: () => {
      S.newCount = 0; S.paused = false; render(); const el = document.getElementById("feedbox"); if (el) el.scrollTop = 0;
    } }));
  if (S.paused) bar.append(h("span", { class: "chip", text: "\\u23f8 paused (hover)" }));
  feed.append(bar);
  const ul = h("ul", { id: "feedbox" });
  const shown = st.events.filter(e => !active || e.event === active).slice(-FEED_MAX).reverse();
  if (!shown.length) ul.append(h("li", {}, h("span", { class: "dim3", text: "no events yet" })));
  for (const ev of shown) {
    ul.append(h("li", {},
      h("span", { class: "ts", text: ((ev.provenance || {}).ts || "").replace("T", " ").slice(0, 19) }),
      h("span", { class: "k", text: ev.event || "?" }),
      h("span", { text: summarize(ev) })));
  }
  feed.append(ul);
  app.append(feed);
}

function gradeChip(t) {
  const cls = { pass: "chip ok", fail: "chip bad", cant_grade: "chip bad", pending: "chip" }[t.graded];
  return h("span", { class: cls, text: t.graded });
}

function renderTrials(app) {
  const st = expState(S.route.exp);
  const p = S.route.params;
  const all = deriveTrials(st);
  const rows = applyFacets(all, p);
  const facetBar = h("div", { class: "toolbar card" });
  const facet = (key, values) => {
    const cur = p.get(key);
    if (cur) {
      /* the chip renders the grammar, negation included [EVAL-19 AC-2] */
      const neg = cur[0] === "-";
      facetBar.append(h("span", { class: "chip click on", tabindex: "0",
        text: (neg ? "-" : "") + key + ": " + (neg ? cur.slice(1) : cur) + " \\u2715",
        onclick: () => setParam(key, null) }));
    } else {
      for (const v of values.slice(0, 6))
        facetBar.append(h("span", { class: "chip click", tabindex: "0", text: key + ": " + v, onclick: () => setParam(key, v) }));
    }
  };
  facet("arm", [...new Set(all.map(t => t.arm))].sort());
  facet("task", [...new Set(all.map(t => t.task_id))].sort());
  facet("outcome", [...new Set(all.map(t => t.outcome))].sort());
  facet("graded", ["pass", "fail", "cant_grade", "pending"]);
  facet("flagged", ["true"]);
  /* the typed grammar: the other projection of the same URL state; a parse
     error is shown in place and the previous filter stays applied */
  const search = h("input", { class: "search gram", id: "gram", spellcheck: "false",
    placeholder: "arm:control -graded:pass task:t1* free text \\u2026 (? explains)",
    value: S.filterErr ? (S.filterRaw || "") : serializeFilter(p) });
  search.addEventListener("change", () => {
    try {
      const parsed = parseFilter(search.value);
      S.filterErr = null; S.filterRaw = null;
      setFacetParams(parsed);
    } catch (err) {
      S.filterErr = String((err && err.message) || err);
      S.filterRaw = search.value;
      render();
    }
  });
  facetBar.append(search);
  facetBar.append(h("span", { class: "chip click" + (p.get("help") ? " on" : ""), tabindex: "0", text: "?",
    onclick: () => setParam("help", p.get("help") ? null : "1") }));
  if (S.filterErr) facetBar.append(h("span", { class: "gerr", id: "gramerr", text: "parse error: " + S.filterErr }));
  facetBar.append(h("span", { class: "spacer" }),
    h("span", { class: "dim3", text: rows.length + " of " + all.length + " trials \\u00b7 filters live in the URL" }));
  app.append(facetBar);
  if (p.get("help")) {
    const help = h("div", { class: "card", id: "gramcard" });
    help.append(h("h2", { text: "Filter grammar (closed \\u2014 anything else is a parse error)" }));
    for (const line of [
      "field:value \\u2014 filter one facet; fields: " + FILTER_FIELDS.join(", "),
      "-field:value \\u2014 negate a field term (a literal leading '-' in a value is not expressible)",
      "task:t1* \\u2014 '*' wildcards on id-like fields (" + WILDCARD_FIELDS.join(", ") + "); wildcarded terms match the whole id",
      "bare words \\u2014 free text over trial/task ids (substring; a word with '*' matches a whole id)",
      "terms are space-separated; one term per field \\u2014 duplicates and unknown fields are parse errors",
    ]) help.append(h("div", { class: "dim", text: line }));
    app.append(help);
  }
  /* saved views: stored URL fragments, local to this browser [EVAL-19 AC-3] */
  const viewsBar = h("div", { class: "toolbar card" });
  viewsBar.append(h("h2", { text: "Views", style: "margin:0" }));
  for (const v of loadViews()) {
    viewsBar.append(h("span", { class: "chip click", tabindex: "0", text: "view: " + v.name,
      onclick: () => { location.hash = v.hash; } }));
    viewsBar.append(h("button", { class: "btn", text: "\\u270e", title: "rename to the typed name",
      onclick: () => renameView(v.name) }));
    viewsBar.append(h("button", { class: "btn", text: "\\u2715", title: "delete this view",
      onclick: () => deleteView(v.name) }));
  }
  const nameIn = h("input", { class: "search", id: "viewname", placeholder: "view name\\u2026" });
  viewsBar.append(nameIn,
    h("button", { class: "btn", id: "viewsave", text: "Save view", onclick: () => saveView(nameIn.value) }));
  if (S.viewsErr) viewsBar.append(h("span", { class: "gerr", id: "viewserr", text: S.viewsErr }));
  viewsBar.append(h("span", { class: "spacer" }),
    h("span", { class: "dim3", text: "a saved view is a stored URL \\u2014 copy the link to share it" }));
  app.append(viewsBar);

  const selId = p.get("sel");
  const wrap = selId ? h("div", { class: "split" }) : h("div");
  const card = h("div", { class: "card" });
  if (!rows.length) card.append(h("div", { class: "empty", text: all.length ? "No trials match these filters." : "No trials on this ledger yet." }));
  else {
    const table = h("table");
    table.append(h("tr", {}, ...["trial", "task", "arm", "rep", "outcome", "grade", "cost", "wall", "flags"].map(x => h("th", { text: x }))));
    rows.forEach((t, i) => {
      const tr = h("tr", { class: "row" + (i === S.sel || t.trial_id === selId ? " sel" : "") });
      tr.addEventListener("click", () => setParam("sel", t.trial_id));
      tr.append(
        h("td", { class: "mono", text: t.trial_id.slice(0, 16) + "\\u2026" }),
        h("td", { text: t.task_id }), h("td", { text: t.arm }), h("td", { class: "mono", text: String(t.repetition) }),
        h("td", {}, h("span", { class: "chip", text: t.outcome })),
        h("td", {}, gradeChip(t)),
        h("td", { class: "mono", text: t.cost === null || t.cost === undefined ? "\\u2014" : fmt(t.cost) }),
        h("td", { class: t.wall === null || t.wall === undefined ? "dim3" : "mono", text: nm(t.wall) }),
        h("td", {}, ...(t.flagged ? [h("span", { class: "chip bad click", tabindex: "0", text: "\\u2691 flag",
                     onclick: (e) => { e.stopPropagation();  /* deep link, not row select [EVAL-19 AC-5] */
                       nav("#/exp/" + encodeURIComponent(S.route.exp) + "/trial/" + encodeURIComponent(t.trial_id) + "?tab=forensics"); } })] : []),
                   ...(t.quarantined ? [h("span", { class: "chip bad", text: "quarantined" })] : []),
                   ...(t.egress ? [h("span", { class: "chip", text: "egress " + t.egress })] : [])));
      table.append(tr);
    });
    card.append(table);
  }
  wrap.append(card);
  if (selId) {
    const panel = h("div", { class: "panel" });
    const t = all.find(x => x.trial_id === selId);
    panel.append(h("h2", { text: selId.slice(0, 22) }));
    if (!t) panel.append(h("div", { class: "dim3", text: "not on this ledger" }));
    else {
      panel.append(h("div", {}, h("b", { text: t.task_id + " / " + t.arm + " \\u00b7 rep " + t.repetition })));
      panel.append(h("div", { class: "dim", text: t.outcome + " \\u00b7 grade: " + t.graded }));
      panel.append(h("div", { class: "dim", text: "cost " + (t.cost === null || t.cost === undefined ? "\\u2014" : fmt(t.cost)) + " \\u00b7 wall " + nm(t.wall) }));
      panel.append(h("div", { style: "margin-top:10px" },
        h("button", { class: "btn", text: "Open full trial \\u21a6 (enter)",
          onclick: () => nav("#/exp/" + encodeURIComponent(S.route.exp) + "/trial/" + encodeURIComponent(selId)) })));
    }
    wrap.append(panel);
  }
  app.append(wrap);
}

function renderTrial(app) {
  const st = expState(S.route.exp);
  const d = st.trial[S.route.id];
  if (!d) { app.append(h("div", { class: "empty", text: "loading trial\\u2026" })); return; }
  const rec = d.record || {};
  const head = h("div", { class: "toolbar card" });
  head.append(h("b", { class: "mono", text: d.trial_id }),
    h("span", { class: "chip", text: rec.task_id + " / " + rec.arm + " \\u00b7 rep " + rec.repetition }),
    h("span", { class: "chip", text: rec.outcome }),
    h("span", { class: "chip" + (d.trajectory.status === "verified" ? " ok" : ""), text: "trajectory " + d.trajectory.status }));
  if (d.quarantine) head.append(h("span", { class: "chip bad", text: "quarantined: " + d.quarantine.reason }));
  head.append(h("span", { class: "spacer" }),
    h("button", { class: "btn", text: "\\u2190 trials", onclick: () => nav("#/exp/" + encodeURIComponent(S.route.exp) + "/trials") }));
  app.append(head);

  const tab = S.route.params.get("tab") || "trajectory";
  const tabs = h("div", { class: "toolbar" });
  for (const name of ["trajectory", "grade", "forensics", "egress", "raw"])
    tabs.append(h("span", { class: "chip click" + (tab === name ? " on" : ""), tabindex: "0", text: name, onclick: () => setParam("tab", name) }));
  app.append(tabs);

  const card = h("div", { class: "card" });
  if (tab === "trajectory") {
    const steps = d.trajectory.steps;
    if (steps === null) card.append(h("div", { class: "empty", text: "trajectory " + d.trajectory.status + " \\u2014 steps unavailable (status is data, not an error)" }));
    else if (!steps.length) card.append(h("div", { class: "empty", text: "trajectory verified, zero steps" }));
    else {
      const ul = h("ul", { class: "steps" });
      for (const s of steps) {
        const headline = s.command ? s.command : (s.files_touched || []).join(", ");
        const li = h("li", {},
          h("span", { class: "t", text: s.relative_ts === null || s.relative_ts === undefined ? "\\u2014" : fmt(s.relative_ts, 0) + "s" }),
          h("span", { class: "ico", text: s.kind }));
        // v3 step content [EVAL-14-D004]: shown when captured; a null detail
        // on a contentless step is the honest pre-v3 / not-exposed state
        const body = h("div", { style: "min-width:0; flex:1" });
        if (headline) body.append(h("div", { class: "mono dim", text: headline }));
        if (s.detail !== null && s.detail !== undefined && s.detail !== "" && s.detail !== headline)
          body.append(h("pre", { class: "code", style: "margin-top:2px", text: s.detail }));
        if (!headline && (s.detail === null || s.detail === undefined))
          body.append(h("div", { class: "mono dim3", text: "(not captured in this record version)" }));
        li.append(body,
          h("span", { class: "dim3", text: (s.exit_code === null || s.exit_code === undefined ? "" : " exit " + s.exit_code) +
            (s.tokens === null || s.tokens === undefined ? "" : " \\u00b7 " + fmt(s.tokens, 0) + " tok") }));
        ul.append(li);
      }
      card.append(ul);
    }
  } else if (tab === "grade") {
    if (!d.grade.grades.length && !d.grade.cant_grades.length) card.append(h("div", { class: "empty", text: "not graded yet" }));
    for (const g of d.grade.grades) {
      card.append(h("div", {}, h("b", { text: "grade: " + (g.binary_score ? "pass" : "fail") }),
        h("span", { class: "dim3", text: "  " + (g.grader || "") + (g.override_of ? " \\u00b7 override" : "") })));
      const table = h("table");
      table.append(h("tr", {}, h("th", { text: "assertion" }), h("th", { text: "source" }), h("th", { text: "result" })));
      for (const a of g.assertions || [])
        table.append(h("tr", {}, h("td", { class: "mono", text: a.id || "" }), h("td", { class: "dim", text: a.source || "" }),
          h("td", {}, h("span", { class: "chip " + (a.result === "pass" ? "ok" : "bad"), text: a.result || "" }))));
      card.append(table);
    }
    for (const c of d.grade.cant_grades)
      card.append(h("div", { class: "dim", text: "cant_grade \\u2192 " + c.reason + (c.override_of ? " (override attempt)" : "") }));
  } else if (tab === "forensics") {
    if (!d.forensics.flags.length && !d.forensics.metrics) card.append(h("div", { class: "empty", text: "no forensics scan covers this trial yet" }));
    for (const f of d.forensics.flags)
      card.append(h("div", {}, h("span", { class: "chip bad", text: f.detector || "flag" }), h("span", { class: "dim", text: " " + (f.reason || "") + " \\u2014 evidence, never a verdict" })));
    if (d.forensics.metrics) card.append(h("pre", { class: "code", text: JSON.stringify(d.forensics.metrics, null, 2) }));
  } else if (tab === "egress") {
    card.append(h("div", { class: "dim", text: "violation: " + nm(d.egress.violation) }));
    for (const a of d.egress.attempts || []) card.append(h("div", { class: "mono", text: a }));
    if (!(d.egress.attempts || []).length) card.append(h("div", { class: "empty", text: "no egress attempts recorded" }));
  } else {
    card.append(h("pre", { class: "code", text: JSON.stringify(d.record, null, 2) }));
  }
  app.append(card);
}

function armHead(c) {
  // label the two-column layout so it's unambiguous which side is arm A vs B
  return h("div", { class: "armhead" },
    h("div", { text: "A \\u00b7 " + c.arm_a }),
    h("div", { text: "B \\u00b7 " + c.arm_b }));
}

function reasoningCol(entries) {
  // Flight recorder (reasoning), grouped by sub-agent role — the client-side
  // mirror of slice_reasoning_by_agent [EVAL-24 AC-6], order preserved.
  const col = h("div", {});
  if (!entries || !entries.length) { col.append(h("div", { class: "none", text: "no reasoning captured" })); return col; }
  const order = [], groups = {};
  for (const e of entries) {
    const role = e.agent || "unattributed";
    if (!(role in groups)) { groups[role] = []; order.push(role); }
    groups[role].push(e);
  }
  for (const role of order) {
    col.append(h("div", { class: "role", text: role }));
    for (const e of groups[role]) col.append(h("div", { class: "reason", text: e.content }));
  }
  return col;
}

function renderCompare(app) {
  const st = expState(S.route.exp);
  const c = st.compare;
  if (!c) { app.append(h("div", { class: "empty", text: "assembling comparisons\\u2026" })); return; }
  if (c.error) { app.append(h("div", { class: "card", text: c.error })); return; }
  const only = S.route.params.get("only") === "disagreements";
  const head = h("div", { class: "toolbar card" });
  head.append(h("b", { text: "Compare: " + c.arm_a + " vs " + c.arm_b + " (baseline: " + c.arm_a + ", lock order)" }),
    h("span", { class: "chip" + (c.official_ready ? " ok" : ""), text: c.official_ready ? "official fence PASSES" : "EXPLORATORY \\u2014 official fence not passed" }));
  head.append(h("span", { class: "spacer" }),
    h("span", { class: "chip click" + (only ? " on" : ""), tabindex: "0", text: "only disagreements (" + c.summary.disagreements + ")" + (only ? " \\u2715" : ""),
      onclick: () => setParam("only", only ? null : "disagreements") }));
  app.append(head);
  /* tallies are navigation [EVAL-19 AC-5]: every count filters to its slice,
     and the slice lives in the URL. Predicates mirror compare.py's summary
     arithmetic exactly, so a tally always equals its filtered row count. */
  const slice = S.route.params.get("slice") || "";
  const knownSlice = ["holdout:a_only", "holdout:b_only", "holdout:both", "holdout:neither",
                      "judge:a", "judge:b", "judge:tie", "judge:cant", "judge:unjudged"];
  const tally = (key, label, count) => h("span", {
    class: "chip click" + (slice === key ? " on" : ""), tabindex: "0",
    text: label + " " + count + (slice === key ? " \\u2715" : ""),
    onclick: () => setParam("slice", slice === key ? null : key) });
  const sm = h("div", { class: "toolbar card" });
  sm.append(h("span", { class: "dim", text: "holdout: " }),
    tally("holdout:a_only", c.arm_a, c.summary.holdout.a_only),
    tally("holdout:b_only", c.arm_b, c.summary.holdout.b_only),
    tally("holdout:both", "both", c.summary.holdout.both),
    tally("holdout:neither", "neither", c.summary.holdout.neither),
    h("span", { class: "dim", text: "\\u2003judge (ADVISORY): " }),
    tally("judge:a", "A", c.summary.judge.a),
    tally("judge:b", "B", c.summary.judge.b),
    tally("judge:tie", "tie", c.summary.judge.tie),
    tally("judge:cant", "cant", c.summary.judge.cant),
    tally("judge:unjudged", "unjudged", c.summary.judge.unjudged),
    h("span", { class: "spacer" }),
    h("span", { class: "dim3", text: "counts filter \\u00b7 the slice lives in the URL" }));
  if (slice && knownSlice.indexOf(slice) < 0)
    sm.append(h("span", { class: "gerr", text: "unknown slice '" + slice + "' \\u2014 ignored" }));
  app.append(sm);
  const slicePred = (p) => {
    const a = p.a.holdout_pass, b = p.b.holdout_pass;
    const w = p.judge ? p.judge.winner : null;
    switch (slice) {
      case "holdout:a_only": return !!a && !b;
      case "holdout:b_only": return !!b && !a;
      case "holdout:both": return !!a && !!b;
      case "holdout:neither": return a === false && b === false;
      case "judge:a": return w === "A";
      case "judge:b": return w === "B";
      case "judge:tie": return w === "TIE";
      case "judge:cant": return w === "CANT_JUDGE";
      case "judge:unjudged": return w === null || w === undefined;
      default: return true;
    }
  };
  const pairs = c.pairs.filter(p => (!only || p.disagreement) && slicePred(p));
  if (!pairs.length) app.append(h("div", { class: "card empty",
    text: slice && knownSlice.indexOf(slice) >= 0 ? "No pairs in this slice."
      : (only ? "No disagreements \\u2014 arms agree on every pair." : "No complete pairs yet (both arms must have a trial per task/rep).") }));
  for (const p of pairs) {
    const card = h("div", { class: "card" });
    const bar = h("div", { class: "toolbar" });
    bar.append(h("b", { text: p.task_id + " \\u00b7 rep " + p.repetition }),
      h("span", { class: "chip " + (p.a.holdout_pass ? "ok" : "bad"), text: c.arm_a + " holdout " + (p.a.holdout_pass === null ? "\\u2014" : (p.a.holdout_pass ? "\\u2713" : "\\u2715")) }),
      h("span", { class: "chip " + (p.b.holdout_pass ? "ok" : "bad"), text: c.arm_b + " holdout " + (p.b.holdout_pass === null ? "\\u2014" : (p.b.holdout_pass ? "\\u2713" : "\\u2715")) }));
    if (p.judge) bar.append(h("span", { class: "chip", text: "judge: " + p.judge.winner + " (ADVISORY)" }));
    if (p.disagreement) bar.append(h("span", { class: "chip", text: "disagreement" }));
    card.append(bar);
    card.append(armHead(c));
    const diff = h("div", { class: "diff2" });
    const left = h("div", {}, h("div", { class: "code" })), right = h("div", {}, h("div", { class: "code" }));
    for (const seg of p.segments) {
      if (seg.op === "equal") { left.firstChild.append(seg.a); right.firstChild.append(seg.b); }
      else {
        if (seg.a) left.firstChild.append(h("span", { class: "del", text: seg.a }));
        if (seg.b) right.firstChild.append(h("span", { class: "add", text: seg.b }));
      }
    }
    if (!p.segments.length) { left.firstChild.textContent = "(empty workspace diff)"; right.firstChild.textContent = "(empty workspace diff)"; }
    diff.append(left, right);
    card.append(diff);
    if (p.judge && p.judge.reason) card.append(h("div", { class: "dim3", style: "margin-top:6px", text: "judge reason: " + p.judge.reason }));
    if ((p.a.reasoning && p.a.reasoning.length) || (p.b.reasoning && p.b.reasoning.length)) {
      card.append(h("div", { class: "dim", style: "margin-top:10px;margin-bottom:4px", text: "Flight recorder \\u00b7 reasoning (operator-tier, unblinded)" }));
      card.append(armHead(c));
      const rz = h("div", { class: "diff2 rz" });
      rz.append(reasoningCol(p.a.reasoning), reasoningCol(p.b.reasoning));
      card.append(rz);
    }
    app.append(card);
  }
}

function renderFindings(app) {
  const st = expState(S.route.exp);
  const f = st.fence;
  if (!f) { app.append(h("div", { class: "empty", text: "checking the fence\\u2026" })); return; }
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Official fence" }),
    h("div", { style: "margin-bottom:8px" }, h("span", { class: "chip" + (f.official_ready ? " ok" : ""),
      text: f.official_ready ? "\\u2713 official render available" : "official render refused \\u2192 exploratory only" })));
  const ul = h("ul", { class: "fence" });
  for (const item of f.items) {
    const mark = { ok: "\\u2713", failed: "\\u2715", unchecked: "\\u25cb" }[item.state] || "?";
    const cls = { ok: "chip ok", failed: "chip bad", unchecked: "chip" }[item.state];
    ul.append(h("li", {}, h("span", { class: cls, text: mark + " " + item.state }),
      h("b", { text: item.name }), h("span", { class: "dim3", text: item.detail || "" })));
  }
  card.append(ul);
  app.append(card);
  const art = h("div", { class: "card" });
  art.append(h("h2", { text: "Rendered artifacts (read-only)" }));
  if (BUNDLE) {
    art.append(h("div", { class: "dim3", text: "artifacts are not embedded in a bundle \\u2014 open findings.* beside the experiment's ledger, or use the live observer" }));
  } else {
    const names = ["findings.json", "findings.exploratory.dossier.html", "findings.official.dossier.html",
                   "findings.exploratory.md", "findings.official.md"];
    const bar = h("div", { class: "toolbar" });
    for (const name of names)
      bar.append(h("button", { class: "btn", text: name, onclick: () =>
        window.open("/artifact?exp=" + encodeURIComponent(S.route.exp) + "&name=" + encodeURIComponent(name), "_blank") }));
    art.append(bar, h("div", { class: "dim3", style: "margin-top:8px",
      text: "buttons open the artifact if analyze has rendered it (404 otherwise, honestly); the dossier is its own self-contained record" }));
  }
  app.append(art);
}

/* ------------------------------------------------------------------ render */
function render() {
  renderBar();
  const app = document.getElementById("app");
  app.textContent = "";
  const r = S.route;
  if (r.screen === "home") renderHome(app);
  else if (r.screen === "exp") renderExp(app);
  else if (r.screen === "trials") renderTrials(app);
  else if (r.screen === "trial") renderTrial(app);
  else if (r.screen === "compare") renderCompare(app);
  else if (r.screen === "findings") renderFindings(app);
}

/* ------------------------------------------------------------------ hover pause */
// Delegated, because renders replace the feed node: the pause state follows
// wherever the pointer actually is, not a listener on a node that may be gone.
document.addEventListener("mouseover", (e) => {
  const inFeed = !!(e.target && e.target.closest && e.target.closest("#feedbox"));
  if (inFeed !== S.paused) {
    S.paused = inFeed;
    if (!inFeed) S.newCount = 0;
    render();
  }
});

/* ------------------------------------------------------------------ keyboard */
document.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
  const r = S.route;
  const lists = { home: () => S.experiments || [], trials: () => applyFacets(deriveTrials(expState(r.exp)), r.params) };
  if (e.key === "j" || e.key === "k") {
    const rows = (lists[r.screen] || (() => []))();
    if (!rows.length) return;
    // trials selection lives in the URL (deep-linkable panel, D002); derive the
    // current index from the sel param — S.sel resets on every hash change.
    let idx = r.screen === "trials"
      ? rows.findIndex(t => t.trial_id === r.params.get("sel"))
      : S.sel;
    idx = Math.max(0, Math.min(rows.length - 1, idx + (e.key === "j" ? 1 : -1)));
    S.sel = idx;
    if (r.screen === "trials") setParam("sel", rows[idx].trial_id); else render();
  } else if (e.key === "Enter") {
    if (r.screen === "home" && (S.experiments || [])[S.sel]) nav("#/exp/" + encodeURIComponent(S.experiments[S.sel].name));
    else if (r.screen === "trials") {
      const sel = r.params.get("sel");
      if (sel) nav("#/exp/" + encodeURIComponent(r.exp) + "/trial/" + encodeURIComponent(sel));
    }
  } else if (e.key === "Escape") {
    if (r.screen === "trials" && r.params.get("sel")) setParam("sel", null);
    else if (r.screen === "trial") nav("#/exp/" + encodeURIComponent(r.exp) + "/trials");
    else if (r.screen !== "home" && r.exp) nav(r.screen === "exp" ? "#/experiments" : "#/exp/" + encodeURIComponent(r.exp));
  }
});

/* ------------------------------------------------------------------ boot */
window.addEventListener("hashchange", () => {
  S.route = parseHash(); S.sel = 0;
  S.filterErr = null; S.filterRaw = null; S.viewsErr = null;  /* navigation clears input errors */
  render(); refresh();
});
S.route = parseHash();
if (BUNDLE) document.getElementById("bundlenote").textContent =
  " Static bundle: an archived, deterministic render of this experiment's ledger \\u2014 nothing here is live and nothing can be changed from this page.";
refresh();
</script>
</body>
</html>
"""
