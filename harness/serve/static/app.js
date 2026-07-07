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
const SPARK_COLORS = ["var(--meter)", "var(--arm-b)"];
const S = {
  experiments: null,          // /api/experiments rows
  exp: {},                    // per-experiment: {events, cursor, status, sel...}
  route: null,
  sel: 0,                     // selected row index on the current list screen
  paused: false, newCount: 0, // feed ergonomics
  feedExpand: new Set(),      // which folded feed runs are expanded (transient drill-down)
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
/* A served JSON error (the observer answered, e.g. 409 chain-broken) is a
   statement about the DATA; only a failed fetch is a statement about the
   SERVER. The two must render differently [fail loudly, precisely]. */
async function j(url) {
  if (BUNDLE) return bundleData(url);
  let r;
  try { r = await fetch(url); }
  catch (e) { const err = new Error(String(e && e.message || e)); err.network = true; throw err; }
  if (!r.ok) throw new Error((await r.json()).error || r.status);
  return r.json();
}
async function refresh() {
  const r = S.route;
  try {
    if (r.screen === "home" || S.experiments === null) {
      S.experiments = (await j("/api/experiments")).experiments;
    }
    if (r.exp) {
      const st = expState(r.exp);
      /* served (non-network) errors are per-experiment data states, shown in
         place by the screen — the observer itself is still online */
      try {
        st.err = null;
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
      } catch (e) {
        if (e.network) throw e;
        st.err = String((e && e.message) || e);
      }
    }
    S.online = true; S.lastError = null;
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
/* relative age for "updated" cells; the absolute ts stays in the title */
function relTime(ts) {
  const t = Date.parse(ts || "");
  if (!isFinite(t)) return null;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  return fmtDur(s) + " ago";
}
/* Fail-closed gate for every ledger-derived screen: a broken chain (or a
   served route error) renders as WITHHELD, never as an empty-but-healthy
   state — "no trials yet" must never describe tampered evidence. */
function withheldCard(st) {
  const chain = st.status && st.status.chain;
  if (chain && chain.ok === false) {
    return h("div", { class: "card gate" },
      h("div", {}, h("b", { text: "⛔ Ledger content withheld — hash chain BROKEN" })),
      h("div", { class: "dim", style: "margin-top:4px", text: chain.detail || "chain failed verification" }),
      h("div", { class: "dim3", style: "margin-top:4px",
        text: "Fail closed: nothing derived from this ledger renders until the chain verifies again. The heartbeat sidecar (outside the chain) stays visible in the header." }));
  }
  if (st.err) {
    return h("div", { class: "card gate softgate" },
      h("div", {}, h("b", { text: "⚠ This view could not load" })),
      h("div", { class: "dim", style: "margin-top:4px", text: st.err }),
      h("div", { class: "dim3", style: "margin-top:4px", text: "The observer is answering; this experiment's read failed. Retrying on the next poll." }));
  }
  return null;
}

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
      if (!t) throw new Error("bare '-' — negation is -field:value");
    }
    const i = t.indexOf(":");
    if (i === 0) throw new Error("missing field name before ':' in '" + tok + "'");
    if (i > 0) {
      const field = t.slice(0, i), value = t.slice(i + 1);
      if (FILTER_FIELDS.indexOf(field) < 0)
        throw new Error("unknown field '" + field + "' — fields: " + FILTER_FIELDS.join(", "));
      if (!value) throw new Error("empty value in '" + tok + "'");
      if (value.indexOf("*") >= 0 && WILDCARD_FIELDS.indexOf(field) < 0)
        throw new Error("'*' wildcards apply to id-like fields only (" + WILDCARD_FIELDS.join(", ") + ")");
      if (field in parsed.fields)
        throw new Error("field '" + field + "' given twice — one term per field");
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
    S.viewsErr = "saved views unreadable — they reset on the next save";
    return [];
  }
}
function persistViews(views) { localStorage.setItem(VIEWS_KEY, JSON.stringify(views)); render(); }
function saveView(name) {
  const views = loadViews();
  const n = (name || "").trim() || ("view-" + (views.length + 1));
  if (views.some(v => v.name === n)) {
    S.viewsErr = "a view named '" + n + "' exists — rename or delete it first"; render(); return;
  }
  S.viewsErr = null;
  views.push({ name: n, hash: location.hash });
  persistViews(views);
}
function renameView(oldName) {
  const el = document.getElementById("viewname");
  const n = ((el && el.value) || "").trim();
  const views = loadViews();
  if (!n) { S.viewsErr = "type the new name, then ✎"; render(); return; }
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
      text: chain.ok ? ("✓ chain OK · " + fmt(chain.events, 0) + " events") : "✕ chain BROKEN" }));
    bar.append(h("span", { class: "chip", text: "judge: ADVISORY" }));
    const hb = st.status.heartbeat;
    if (hb) {
      let label = "heartbeat: " + hb.state, cls = "chip" + (hb.state === "finished" ? " ok" : "");
      const age = Date.now() - Date.parse(hb.ts);
      if (hb.state === "running" && isFinite(age) && age > 90000)
        { label = "⚠ heartbeat stale " + fmtDur(Math.round(age / 1000)) + " — crashed run?"; cls = "chip warn"; }
      bar.append(h("span", { class: cls, text: label }));
    }
  }
  if (r.exp) {
    bar.append(h("span", { class: "spacer" }));
    for (const [label, screen] of [["overview", ""], ["trials", "/trials"], ["compare", "/compare"], ["findings", "/findings"]]) {
      const on = (screen === "" && r.screen === "exp") || screen.slice(1) === r.screen;
      bar.append(h("span", { class: "chip click" + (on ? " on" : ""), tabindex: "0", text: label,
        onclick: () => nav("#/exp/" + encodeURIComponent(r.exp) + screen) }));
    }
  }
  bar.append(h("span", { class: r.exp ? "" : "spacer" }));
  if (BUNDLE) bar.append(h("span", { class: "chip" + (S.online === false ? " bad" : ""),
    text: "STATIC BUNDLE" + (S.online === false ? " · " + (S.lastError || "")
                                                : " · archived snapshot, does not update") }));
  else bar.append(h("span", { class: "chip " + (S.online ? "ok" : "bad"),
    text: S.online ? "live" : ("unreachable: " + (S.lastError || "")) }));
}

/* ------------------------------------------------------------------ screens */
/* One truthful lifecycle label per home row. "Locked, nothing run yet" and
   "no runnable plan" are different states and must not share the word
   "unplanned"; a running heartbeat gone silent is a warning, not "running". */
function homeState(e) {
  if (e.chain && e.chain.ok === false) return { text: "✕ chain broken", cls: " bad" };
  const sm = e.summary;
  if (e.heartbeat_state === "running") {
    const age = Date.now() - Date.parse(e.heartbeat_ts || "");
    if (isFinite(age) && age > 90000)
      return { text: "⚠ stale · silent " + fmtDur(Math.round(age / 1000)), cls: " warn" };
    return { text: "running", cls: " ok" };
  }
  if (e.heartbeat_state === "stopped_cost_ceiling") return { text: "stopped: cost ceiling", cls: " warn" };
  if (e.heartbeat_state === "finished") return { text: "finished", cls: "" };
  if (e.heartbeat_state) return { text: e.heartbeat_state, cls: "" };
  if (!sm) return { text: "—", cls: "" };
  if (sm.cells.planned === null) return { text: sm.cells.done ? "idle" : "no plan yet", cls: "" };
  if (!sm.cells.done) return { text: "ready · locked", cls: "" };
  if (sm.cells.done < sm.cells.planned) return { text: "partial · idle", cls: "" };
  return { text: "run complete", cls: "" };
}
function renderHome(app) {
  const rows = S.experiments || [];
  const card = h("div", { class: "card" });
  card.append(h("div", { class: "eyebrow", style: "margin-bottom:8px", text: "Experiments" }));
  if (!rows.length) { card.append(h("div", { class: "empty", text: "No experiment directories (with a ledger.ndjson) found under this root." })); app.append(card); return; }
  const table = h("table");
  table.append(h("tr", {}, ...["experiment", "state", "arms", "cells", "", "spend", "graded", "judged", "selfcheck", "updated"].map(t => h("th", { text: t }))));
  rows.forEach((e, i) => {
    const sm = e.summary;
    const tr = h("tr", { class: "row" + (i === S.sel ? " sel" : "") });
    tr.addEventListener("click", () => nav("#/exp/" + encodeURIComponent(e.name)));
    tr.append(h("td", {}, h("b", { text: e.name })));
    const state = homeState(e);
    tr.append(h("td", {}, h("span", { class: "chip" + state.cls, text: state.text })));
    if (!sm) {
      /* fail closed: a broken chain withholds every ledger-derived cell */
      tr.append(...Array.from({ length: 8 }, (_x, k) =>
        h("td", { class: "dim3", text: k < 2 ? "withheld" : "—" })));
    } else {
      const armsTd = h("td", {});
      if (sm.arms && sm.arms.length) {
        /* the arm identity dots ride the names here too — A is the spec's
           first arm, matching compare and the verdict card [operator-ui §Tokens] */
        const line = h("div", {});
        sm.arms.forEach((a, k) => {
          if (k) line.append(" vs ");
          if (k < 2) line.append(h("span", { class: "armdot", style: "background: var(" + (k === 0 ? "--arm-a" : "--arm-b") + ")" }));
          line.append(a);
        });
        armsTd.append(line);
        const models = (sm.arms || []).map(a => shortModel((sm.arm_models || {})[a])).filter(Boolean);
        if (models.length) armsTd.append(h("div", { class: "dim3", text: models.join(" vs ") }));
      } else armsTd.append(h("span", { class: "dim3", text: "—" }));
      tr.append(armsTd);
      tr.append(h("td", { class: "mono", text: fmt(sm.cells.done, 0) + "/" + fmt(sm.cells.planned, 0) }));
      const meter = h("div", { class: "meter", style: "width:76px" }, h("div"));
      if (sm.cells.planned) meter.firstChild.style.width = Math.min(100, 100 * sm.cells.done / sm.cells.planned) + "%";
      tr.append(h("td", {}, meter));
      const spendTd = h("td", { class: "mono", text: fmt(sm.spend.accumulated) + "/" + fmt(sm.spend.ceiling) });
      if (sm.spend.currency) spendTd.append(h("span", { class: "dim3", text: " " + sm.spend.currency }));
      tr.append(spendTd);
      const gradedTd = h("td", { class: "mono", text: fmt(sm.grade.graded, 0) + "/" + fmt(sm.cells.done, 0) });
      if (sm.grade.pending) gradedTd.append(h("span", { class: "chip warn", style: "margin-left:6px", text: fmt(sm.grade.pending, 0) + " pending" }));
      tr.append(gradedTd);
      tr.append(h("td", { class: "mono", text: fmt(sm.judge.verdicts, 0) + "/" + fmt(sm.judge.pairs_expected, 0) }));
      tr.append(h("td", {}, h("span", { class: "chip" + (sm.selfcheck === "current" ? " ok" : (sm.selfcheck === "stale" ? " warn" : "")), text: sm.selfcheck })));
      const rel = relTime(sm.last_event_ts);
      tr.append(h("td", { class: "dim3", title: sm.last_event_ts || "",
        text: rel || (sm.last_event_ts || "").replace("T", " ").slice(0, 19) || "—" }));
    }
    table.append(tr);
  });
  card.append(table);
  app.append(card);
}

/* operator vocabulary for ledger event kinds [DESIGN feed humanization]: the
   feed shows these words, the internal kind stays in each row's title, and the
   filter logic still keys on the internal kind. Unknown kinds fall through. */
const KIND_DISPLAY = {
  trial: "trial finished", grade: "graded", cant_grade: "grading refused",
  judge_verdict: "judge verdict (advisory)", human_verdict: "human verdict",
  process_score: "process scored", trial_infra_failed: "infra failure",
  experiment_locked: "plan locked", executed_order: "run order realized",
  forensics_report: "forensics scan", forensic_quarantine: "quarantined",
  forensic_spotcheck: "spot check", selfcheck: "selfcheck",
  calibration_run: "calibration", findings_rendered: "findings rendered",
  cant_analyze: "analysis refused", review_packet_built: "review packet",
  reveal: "reveal", task_admitted: "task admitted",
  contamination_probe: "contamination probe", chain_anchor: "chain anchored",
  run_stopped_cost_ceiling: "stopped: cost ceiling",
};
function kindDisplay(k) { return KIND_DISPLAY[k] || k || "?"; }

function summarize(ev) {
  const rec = ev.trial_record || {};
  switch (ev.event) {
    case "trial": return rec.task_id + " / " + rec.arm + " rep" + rec.repetition + " → " + rec.outcome;
    case "trial_infra_failed": return ev.task_id + " / " + ev.arm + " → " + ev.reason;
    case "grade": return ev.trial_id + " → " + (ev.binary_score ? "pass" : "fail");
    case "cant_grade": return ev.trial_id + " → " + ev.reason;
    case "judge_verdict": { const v = ev.verdict || {}; return (v.comparison_id || "") + " → " + (v.winner || ""); }
    case "run_stopped_cost_ceiling": return "spend " + fmt(ev.accumulated_cost) + " of " + fmt(ev.ceiling);
    case "experiment_locked": return "seed " + ev.seed + ", spec " + String(ev.spec_sha256 || "").slice(0, 12);
    case "executed_order": return fmt((ev.order || []).length, 0) + " cell(s) realized";
    case "forensics_report": { const fr = ev.forensics_report || {}; const cov = fr.coverage || {};
      return fmt((fr.flags || []).length, 0) + " flag(s) · coverage " + fmt(cov.covered, 0) + "/" + fmt(cov.trials, 0); }
    case "forensic_quarantine": { const q = ev.forensic_quarantine || {};
      return (q.trial_id || "") + " — " + (q.reason || ""); }
    case "forensic_spotcheck": return ev.trial_id || "";
    case "selfcheck": return (ev.passed ? "passed" : "FAILED") + " · " + (ev.selected_method || "")
      + " · coverage " + fmt(ev.coverage);
    case "calibration_run": return (ev.corpus_id || "") + "@" + (ev.semver || "") + " → " + (ev.status || "");
    case "findings_rendered": return (ev.mode || "") + " render";
    case "cant_analyze": return ev.reason || "";
    case "review_packet_built": return ev.packet_id || "";
    case "human_verdict": { const v = ev.verdict || {}; return (v.comparison_id || ev.comparison_id || "") + " → " + (v.winner || ev.winner || ""); }
    case "reveal": return ev.comparison_id || "";
    case "task_admitted": return ev.task_id || "";
    case "contamination_probe": return ev.task_id || ev.probe_id || "";
    case "chain_anchor": return "head " + String(ev.head_hash || "").slice(0, 12);
    default: return "";
  }
}
/* the deep link a feed row carries: events about a trial open that trial;
   verdicts open the compare screen [tallies-are-navigation precedent] */
function feedTarget(ev) {
  const exp = encodeURIComponent(S.route.exp);
  if (ev.event === "trial" && (ev.trial_record || {}).trial_id)
    return "#/exp/" + exp + "/trial/" + encodeURIComponent(ev.trial_record.trial_id);
  if ((ev.event === "grade" || ev.event === "cant_grade") && ev.trial_id)
    return "#/exp/" + exp + "/trial/" + encodeURIComponent(ev.trial_id) + "?tab=grade";
  if (ev.event === "forensic_quarantine" && (ev.forensic_quarantine || {}).trial_id)
    return "#/exp/" + exp + "/trial/" + encodeURIComponent(ev.forensic_quarantine.trial_id) + "?tab=forensics";
  if (ev.event === "judge_verdict" || ev.event === "human_verdict") return "#/exp/" + exp + "/compare";
  if (ev.event === "forensics_report") return "#/exp/" + exp + "/trials?flagged=true";
  return null;
}
function feedScrolled() { const el = document.getElementById("feedbox"); return !!el && el.scrollTop > 8; }
/* one ledger event as a feed row: the operator-facing kind name shows, the
   internal kind stays in the title, and the row deep-links where the event
   lives [DESIGN feed humanization] */
function feedRow(ev) {
  const target = feedTarget(ev);
  const li = h("li", { class: target ? "click" : "" },
    h("span", { class: "ts", text: ((ev.provenance || {}).ts || "").replace("T", " ").slice(0, 19) }),
    h("span", { class: "k", title: ev.event || "", text: kindDisplay(ev.event) }),
    h("span", { text: summarize(ev) }));
  if (target) li.addEventListener("click", () => nav(target));
  return li;
}
/* a folded run of ≥3 same-kind events — "process scored × 16" with the newest
   timestamp; clicking expands the run in place. The expansion set is transient
   (a drill-down, fine to lose on the next poll re-render). */
function toggleFeedRun(key) {
  if (S.feedExpand.has(key)) S.feedExpand.delete(key); else S.feedExpand.add(key);
  render();
}
function feedRunHeader(run, key, open) {
  const newest = run[0];
  const li = h("li", { class: "click", tabindex: "0",
    onkeydown: (e) => { if (e.key === "Enter") toggleFeedRun(key); } },
    h("span", { class: "ts", text: ((newest.provenance || {}).ts || "").replace("T", " ").slice(0, 19) }),
    h("span", { class: "k", title: newest.event || "", text: (open ? "▾ " : "▸ ") + kindDisplay(newest.event) + " × " + run.length }),
    h("span", { class: "dim3", text: open ? "newest first — click to fold" : "click to expand" }));
  li.addEventListener("click", () => toggleFeedRun(key));
  return li;
}

/* The per-pair model behind BOTH the overview tallies and the pair tape: one
   record per (task, repetition) pair in first-seen order, plus the aggregate
   tally. Mirrors compare.py's summary arithmetic EXACTLY (the slicePred
   precedent) over the same ledger events, so the tape cells and the tally
   counts are always the same numbers — that agreement is a product invariant. */
function pairModel(st) {
  const spec = st.status && st.status.stages && st.status.stages.spec;
  if (!spec || !spec.arms || spec.arms.length < 2) return null;
  const [armA, armB] = spec.arms;
  const trials = {}, grades = {}, winners = {};
  for (const ev of st.events) {
    if (ev.event === "trial") { const r = ev.trial_record || {};
      trials[r.task_id + "\u0000" + r.repetition + "\u0000" + r.arm] = r.trial_id; }
    else if (ev.event === "grade") grades[ev.trial_id] = ev.binary_score;
    else if (ev.event === "judge_verdict") { const v = ev.verdict || {};
      winners[v.comparison_id] = v.winner; }
  }
  /* cells in first-seen order (Object.keys preserves insertion), so the tape
     reads left→right the way the ledger filled the grid */
  const cellKeys = [], seen = new Set();
  for (const k of Object.keys(trials)) { const p = k.split("\u0000");
    const cell = p[0] + "\u0000" + p[1];
    if (!seen.has(cell)) { seen.add(cell); cellKeys.push(cell); }
  }
  const t = { a: 0, b: 0, both: 0, neither: 0, graded_pairs: 0, pairs: 0,
              ja: 0, jb: 0, jtie: 0, jcant: 0, junjudged: 0 };
  const cells = [];
  for (const key of cellKeys) {
    const [task, rep] = key.split("\u0000");
    const ta = trials[task + "\u0000" + rep + "\u0000" + armA], tb = trials[task + "\u0000" + rep + "\u0000" + armB];
    if (ta === undefined || tb === undefined) continue;  /* not a pair: one arm has no trial */
    t.pairs += 1;
    const a = grades[ta], b = grades[tb];
    let state = "ungraded";  /* not fully graded — dashed cell, excluded from tallies */
    if (a !== undefined && b !== undefined && a !== null && b !== null) {
      t.graded_pairs += 1;
      if (a && b) { t.both += 1; state = "both"; }
      else if (a) { t.a += 1; state = "a"; }
      else if (b) { t.b += 1; state = "b"; }
      else { t.neither += 1; state = "neither"; }
    }
    cells.push({ task, rep, state });
    /* "cmp-<task>-r<rep>" mirrors judge.assemble.comparison_id_for */
    const w = winners["cmp-" + task + "-r" + rep];
    if (w === "A") t.ja += 1; else if (w === "B") t.jb += 1;
    else if (w === "TIE") t.jtie += 1; else if (w === "CANT_JUDGE") t.jcant += 1;
    else t.junjudged += 1;
  }
  return { armA, armB, cells, t };
}
/* the aggregate tally alone — the verdict card's guard and its counts
   [unchanged public shape: {armA, armB, t}] */
function pairTallies(st) {
  const m = pairModel(st);
  return m ? { armA: m.armA, armB: m.armB, t: m.t } : null;
}
/* The pair tape [operator-ui §Signature]: the experiment's sign test drawn as a
   strip of cells, one 14×14 cell per (task, rep) pair grouped by task. Every
   cell's state comes from pairModel — the SAME derivation as the tallies — so
   the tape and the counts can never disagree. A one-line legend renders beneath
   always (relief rule: color never carries the state alone). */
function pairTape(st, opts) {
  opts = opts || {};
  const model = pairModel(st);
  if (!model || !model.cells.length) return null;
  const { armA, armB, cells } = model;
  const exp = encodeURIComponent(S.route.exp);
  const stateText = { a: "only " + armA + " passed", b: "only " + armB + " passed",
                      both: "both passed", neither: "neither passed", ungraded: "not fully graded" };
  /* group cells by task in first-seen order; a gutter separates groups, the
     task id is labelled beneath when there are few enough groups to read */
  const order = [], groups = {};
  for (const cell of cells) {
    if (!(cell.task in groups)) { groups[cell.task] = []; order.push(cell.task); }
    groups[cell.task].push(cell);
  }
  const showTids = opts.labels !== false && order.length <= 24;
  const wrap = h("div", {});
  const tape = h("div", { class: "tape" });
  for (const task of order) {
    const grp = h("div", { class: "tape-group" });
    const row = h("div", { class: "tape-cells" });
    for (const cell of groups[task])
      /* one navigable cell — click/enter/space all reach its pair; a caller
         already on the compare screen supplies onCell to open in place */
      row.append(h("button", { type: "button", class: "tape-cell " + cell.state, tabindex: "0",
        title: cell.task + " · rep " + cell.rep + " — " + stateText[cell.state],
        onclick: opts.onCell ? (() => opts.onCell(cell))
                             : (() => nav("#/exp/" + exp + "/compare")) }));
    grp.append(row);
    if (showTids) grp.append(h("div", { class: "tape-tid", title: task, text: task }));
    tape.append(grp);
  }
  wrap.append(tape);
  const legend = h("div", { class: "tape-legend" });
  const item = (cls, label) => legend.append(h("span", {},
    h("span", { class: "swatch " + cls }), label));
  item("a", armA + " only"); item("b", armB + " only"); item("both", "both");
  item("neither", "neither"); item("ungraded", "ungraded");
  wrap.append(legend);
  return wrap;
}
function renderExp(app) {
  const st = expState(S.route.exp), snap = st.status;
  if (!snap) { app.append(withheldCard(st) || h("div", { class: "empty", text: "loading…" })); return; }
  const stg = snap.stages;
  if (!stg) { app.append(withheldCard(st) || h("div", { class: "card", text: "Ledger content withheld: " + ((snap.chain || {}).detail || "chain failed verification") + " [fail closed]" })); return; }

  const exp = encodeURIComponent(S.route.exp);
  const rail = h("div", { class: "rail" });
  /* every stage card is a doorway into the screen that shows its work */
  const stages = [
    ["run", fmt(stg.cells.done, 0) + "/" + fmt(stg.cells.planned, 0) + (snap.heartbeat && snap.heartbeat.in_flight ? " · in flight" : ""),
     "#/exp/" + exp + "/trials"],
    ["grade", fmt(stg.grade.graded, 0) + " graded · " + fmt(stg.grade.pending, 0) + " pending",
     "#/exp/" + exp + "/trials" + (stg.grade.pending ? "?graded=pending" : "")],
    ["judge", fmt(stg.judge.verdicts, 0) + "/" + fmt(stg.judge.pairs_expected, 0) + " pairs"
     + (stg.judge.cant_judge ? " · " + fmt(stg.judge.cant_judge, 0) + " cant" : ""),
     "#/exp/" + exp + "/compare"],
    ["review", fmt(stg.review.human_verdicts, 0) + "/" + fmt(stg.review.packets, 0), null],
    ["forensics", fmt(stg.forensics.reports, 0) + " scan" + (stg.forensics.latest ? " · " + stg.forensics.latest.flags + " flags" : ""),
     stg.forensics.latest && stg.forensics.latest.flags ? "#/exp/" + exp + "/trials?flagged=true" : null],
    ["analyze", "selfcheck " + stg.analyze.selfcheck, "#/exp/" + exp + "/findings"],
  ];
  for (const [name, val, target] of stages) {
    const cell = h("div", { class: "stage" + (target ? " click" : "") },
      h("div", { class: "nm", text: name + (target ? " →" : "") }),
      h("div", { class: "st", text: val }));
    if (target) { cell.setAttribute("tabindex", "0"); cell.addEventListener("click", () => nav(target)); }
    rail.append(cell);
  }
  /* verdict card first [operator-ui §Overview]: the deterministic-first result made
     the loudest thing on the screen — the same numbers compare shows, one nav
     away. Renders only when there are pairs (same guard as the strip it
     replaced). It is appended BEFORE the rail so the order reads verdict → rail
     → tiles → feed. */
  const pt = pairTallies(st);
  if (pt && pt.t.pairs) {
    const { armA, armB, t } = pt;
    const armModels = (stg.spec || {}).arm_models || {};
    const aPass = t.a + t.both, bPass = t.b + t.both;
    const card = h("div", { class: "card verdict" });
    card.append(h("div", { class: "eyebrow",
      text: "Result so far · holdout (deterministic) · " + t.pairs + " pairs" }));
    const nums = h("div", { class: "verdict-nums" });
    /* each arm: an 8px identity dot, the 32px pass count over its graded
       denominator, then the arm name + the model it ran */
    const armBlock = (name, model, pass, dot) => {
      const arm = h("div", { class: "verdict-arm" });
      arm.append(h("div", { class: "verdict-top" },
        h("span", { class: "verdict-dot", style: "background:" + dot }),
        h("span", { class: "verdict-count", text: t.graded_pairs ? String(pass) : "—" }),
        h("span", { class: "verdict-denom", text: "/" + t.graded_pairs })));
      const sub = h("div", { class: "verdict-sub" }, h("b", { text: name }));
      if (model) sub.append(h("span", { class: "dim3", text: " · " + shortModel(model) }));
      arm.append(sub);
      return arm;
    };
    nums.append(armBlock(armA, armModels[armA], aPass, "var(--arm-a)"),
                armBlock(armB, armModels[armB], bPass, "var(--arm-b)"));
    /* leading is identity, not virtue — weight carries it, never green/red */
    if (!t.graded_pairs) nums.append(h("div", { class: "verdict-lead none", text: "no fully graded pairs yet" }));
    else { const lead = aPass > bPass ? armA : (bPass > aPass ? armB : null);
      nums.append(h("div", { class: "verdict-lead", text: lead ? "→ " + lead + " leads" : "→ tie" })); }
    card.append(nums);
    const tape = pairTape(st);
    if (tape) card.append(tape);
    /* one quiet advisory line — the judge never outranks the holdout, and the
       ADVISORY label rides every judge readout [honesty invariant] */
    const judged = t.ja + t.jb + t.jtie + t.jcant;
    const judge = h("div", { class: "verdict-judge" }, h("span", { class: "eyebrow", text: "Judge" }));
    if (judged) {
      const jLean = t.jb > t.ja ? "leans " + armB : (t.ja > t.jb ? "leans " + armA : "tie/mixed");
      judge.append(armA + " " + t.ja + " · " + armB + " " + t.jb + " · tie " + t.jtie + " → " + jLean + " (ADVISORY)");
    } else judge.append(h("span", { class: "dim3", text: "no verdicts yet (ADVISORY)" }));
    card.append(judge);
    /* honest denominator: pairs excluded from the tallies are named, not hidden */
    const ungraded = t.pairs - t.graded_pairs;
    if (ungraded > 0) card.append(h("div", { class: "verdict-excluded",
      text: ungraded + " of " + t.pairs + " pair(s) not fully graded — excluded from tallies" }));
    app.append(card);
  }
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
  if (eta) cellsTile.append(h("div", { class: "sub", text: "≈ " + fmtDur(eta.seconds) +
    " left · approximate, from " + eta.sample + " completion timestamps" }));
  tiles.append(cellsTile);

  const spendTile = h("div", { class: "card tile" });
  spendTile.append(h("div", { class: "label", text: "Spend vs ceiling" }),
    h("div", { class: "value", text: fmt(stg.spend.accumulated) + " / " + fmt(stg.spend.ceiling) }));
  const m2 = h("div", { class: "meter" }, h("div"));
  if (stg.spend.ceiling) m2.firstChild.style.width = Math.min(100, 100 * stg.spend.accumulated / stg.spend.ceiling) + "%";
  const perArm = Object.entries(stg.per_arm).map(([a, c]) => a + " " + fmt(c.cost)).join(" · ");
  spendTile.append(m2, h("div", { class: "sub", text: (stg.spend.currency || "") + (perArm ? " · " + perArm : "") + (stg.spend.stopped_cost_ceiling ? " · STOPPED" : "") }));
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
    const elapsed = isFinite(started) ? fmtDur(Math.max(0, Math.round((Date.now() - started) / 1000))) : "";
    flightTile.append(h("div", { class: "value", text: flight.task_id + " / " + flight.arm }),
      h("div", { class: "sub", text: "rep " + flight.repetition + ", attempt " + flight.attempt + (elapsed ? ", running " + elapsed : "") }));
  } else {
    /* say what the run is actually doing, not a bare "idle" */
    const hbState = snap.heartbeat && snap.heartbeat.state;
    const label = { running: "idle between trials", finished: "run finished",
                    stopped_cost_ceiling: "stopped: cost ceiling" }[hbState];
    flightTile.append(h("div", { class: "value", text: label || (stg.cells.done ? "idle" : "not started") }));
  }
  tiles.append(flightTile);

  const armsTile = h("div", { class: "card tile" });
  armsTile.append(h("div", { class: "label", text: "Per-arm trials (unblinded)" }));
  const armModels = (stg.spec || {}).arm_models || {};
  const armEntries = Object.entries(stg.per_arm);
  if (!armEntries.length) armsTile.append(h("div", { class: "sub dim3", text: "no trials yet" }));
  for (const [arm, c] of armEntries) {
    const head = h("div", { class: "sub" }, h("b", { text: arm }));
    if (armModels[arm]) head.append(h("span", { class: "dim3", text: " · " + shortModel(armModels[arm]) }));
    armsTile.append(head);
    armsTile.append(h("div", { class: "sub dim3", style: "margin-bottom:4px",
      text: c.trials + " trial(s) — " + c.completed + " completed · " + c.timeout
        + " timeout · " + c.infra_failed + " infra-failed" }));
  }
  if (stg.quarantines.length) armsTile.append(h("div", { class: "sub", text: "⚠ quarantined: " + stg.quarantines.length }));
  tiles.append(armsTile);
  app.append(tiles);

  /* feed demotion [DESIGN]: the ledger feed is a drill-down now, collapsed to a
     one-line disclosure whose open flag rides the URL — a poll re-render (every
     POLL_MS) can never slam it shut mid-read. */
  const feedOpen = S.route.params.get("feed") === "1";
  const kindsPresent = [...new Set(st.events.map(e => e.event).filter(Boolean))];
  const feed = h("div", { class: "card" });
  const feedhead = h("div", { class: "feedhead", tabindex: "0",
    onkeydown: (e) => { if (e.key === "Enter") setParam("feed", feedOpen ? null : "1"); } },
    h("span", { class: "chev", text: feedOpen ? "▾" : "▸" }),
    h("span", { class: "eyebrow", text: "Ledger feed" }),
    h("span", { class: "dim3", text: "· " + st.events.length + " events · " + kindsPresent.length + " kinds" }));
  feedhead.addEventListener("click", () => setParam("feed", feedOpen ? null : "1"));
  feed.append(feedhead);
  if (feedOpen) {
    const bar = h("div", { class: "toolbar", style: "margin-top:8px" });
    /* the workhorse kinds get fixed chips; any other kind actually on this
       ledger gets one too, so no event class is only reachable via "all". Chip
       labels are humanized; the filter still keys on the internal kind. */
    const kinds = ["trial", "grade", "judge_verdict", "cant_grade", "trial_infra_failed"];
    const extra = [...new Set(st.events.map(e => e.event))]
      .filter(k => k && kinds.indexOf(k) < 0).sort().slice(0, 8);
    const active = S.route.params.get("kind") || "";
    bar.append(h("span", { class: "chip click" + (!active ? " on" : ""), tabindex: "0", text: "all", onclick: () => setParam("kind", null) }));
    for (const k of kinds.concat(extra))
      bar.append(h("span", { class: "chip click" + (active === k ? " on" : ""), tabindex: "0",
        title: k, text: kindDisplay(k), onclick: () => setParam("kind", k) }));
    bar.append(h("span", { class: "spacer" }));
    if (S.newCount > 0)
      bar.append(h("button", { class: "pill", text: S.newCount + " new ↑", onclick: () => {
        S.newCount = 0; S.paused = false; render(); const el = document.getElementById("feedbox"); if (el) el.scrollTop = 0;
      } }));
    if (S.paused) bar.append(h("span", { class: "chip", text: "⏸ paused (hover)" }));
    feed.append(bar);
    const ul = h("ul", { id: "feedbox" });
    const shown = st.events.filter(e => !active || e.event === active).slice(-FEED_MAX).reverse();
    if (!shown.length) ul.append(h("li", {}, h("span", { class: "dim3", text: "no events yet" })));
    else if (active) {
      /* a kind is already isolated — show every row, do not fold the filtered
         stream (which is one long run) behind a single collapse line */
      for (const ev of shown) ul.append(feedRow(ev));
    } else {
      /* fold consecutive runs (≥3) of one kind into a single expandable row, so
         a burst reads as "process scored × 16", not sixteen identical lines */
      let i = 0;
      while (i < shown.length) {
        let j = i;
        while (j < shown.length && shown[j].event === shown[i].event) j += 1;
        const run = shown.slice(i, j);
        if (run.length >= 3) {
          const newest = run[0];
          const key = run[0].event + " " + ((newest.provenance || {}).ts || "") + " " + run.length;
          const open = S.feedExpand.has(key);
          ul.append(feedRunHeader(run, key, open));
          if (open) for (const ev of run) ul.append(feedRow(ev));
        } else for (const ev of run) ul.append(feedRow(ev));
        i = j;
      }
    }
    feed.append(ul);
  }
  app.append(feed);
}

function gradeChip(t) {
  const cls = { pass: "chip ok", fail: "chip bad", cant_grade: "chip bad", pending: "chip" }[t.graded];
  const word = { cant_grade: "can't grade" }[t.graded] || t.graded;
  return h("span", { class: cls, title: t.graded, text: word });
}

/* the one ordering both the table and the j/k keyboard walk use; default is
   ledger order, ?sort=[-]field is URL state like every other view knob */
const SORT_FIELDS = ["task", "arm", "rep", "outcome", "grade", "cost", "wall"];
function sortTrials(rows, p) {
  const raw = p.get("sort");
  if (!raw) return rows;
  const desc = raw[0] === "-", field = desc ? raw.slice(1) : raw;
  if (SORT_FIELDS.indexOf(field) < 0) return rows;
  const key = { task: t => t.task_id, arm: t => t.arm, rep: t => t.repetition,
                outcome: t => t.outcome, grade: t => t.graded,
                cost: t => t.cost, wall: t => t.wall }[field];
  return rows.slice().sort((x, y) => {
    const a = key(x), b = key(y);
    /* unmeasured sorts last in either direction — absence is not a value */
    if (a === null || a === undefined) return (b === null || b === undefined) ? 0 : 1;
    if (b === null || b === undefined) return -1;
    const c = typeof a === "number" ? a - b : String(a).localeCompare(String(b));
    return desc ? -c : c;
  });
}
function visibleTrials(st, p) { return sortTrials(applyFacets(deriveTrials(st), p), p); }

function renderTrials(app) {
  const st = expState(S.route.exp);
  const p = S.route.params;
  const gate = withheldCard(st);
  if (gate) { app.append(gate); return; }
  const all = deriveTrials(st);
  const rows = visibleTrials(st, p);
  /* one filter card, two rows [operator-ui §Everywhere]: the facet chips + typed
     grammar above, saved views below — the same closed grammar and the same
     URL state as before, with less chrome between the operator and the table */
  const filters = h("div", { class: "card" });
  const facetBar = h("div", { class: "toolbar" });
  const facet = (key, values) => {
    const cur = p.get(key);
    if (cur) {
      /* the chip renders the grammar, negation included [EVAL-19 AC-2] */
      const neg = cur[0] === "-";
      facetBar.append(h("span", { class: "chip click on", tabindex: "0",
        text: (neg ? "-" : "") + key + ": " + (neg ? cur.slice(1) : cur) + " ✕",
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
    placeholder: "arm:control -graded:pass task:t1* free text … (? explains)",
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
    h("span", { class: "dim3", text: rows.length + " of " + all.length + " trials · filters live in the URL" }));
  filters.append(facetBar);
  /* saved views: stored URL fragments, local to this browser [EVAL-19 AC-3] */
  const viewsBar = h("div", { class: "toolbar",
    style: "margin-top:8px; padding-top:8px; border-top: 1px solid var(--hairline)" });
  viewsBar.append(h("span", { class: "eyebrow", text: "views" }));
  for (const v of loadViews()) {
    viewsBar.append(h("span", { class: "chip click", tabindex: "0", text: "view: " + v.name,
      onclick: () => { location.hash = v.hash; } }));
    viewsBar.append(h("button", { class: "btn", text: "✎", title: "rename to the typed name",
      onclick: () => renameView(v.name) }));
    viewsBar.append(h("button", { class: "btn", text: "✕", title: "delete this view",
      onclick: () => deleteView(v.name) }));
  }
  const nameIn = h("input", { class: "search", id: "viewname", placeholder: "view name…" });
  viewsBar.append(nameIn,
    h("button", { class: "btn", id: "viewsave", text: "Save view", onclick: () => saveView(nameIn.value) }));
  if (S.viewsErr) viewsBar.append(h("span", { class: "gerr", id: "viewserr", text: S.viewsErr }));
  viewsBar.append(h("span", { class: "spacer" }),
    h("span", { class: "dim3", text: "a saved view is a stored URL — copy the link to share it" }));
  filters.append(viewsBar);
  app.append(filters);
  if (p.get("help")) {
    const help = h("div", { class: "card", id: "gramcard" });
    help.append(h("h2", { text: "Filter grammar (closed — anything else is a parse error)" }));
    for (const line of [
      "field:value — filter one facet; fields: " + FILTER_FIELDS.join(", "),
      "-field:value — negate a field term (a literal leading '-' in a value is not expressible)",
      "task:t1* — '*' wildcards on id-like fields (" + WILDCARD_FIELDS.join(", ") + "); wildcarded terms match the whole id",
      "bare words — free text over trial/task ids (substring; a word with '*' matches a whole id)",
      "terms are space-separated; one term per field — duplicates and unknown fields are parse errors",
    ]) help.append(h("div", { class: "dim", text: line }));
    app.append(help);
  }

  const selId = p.get("sel");
  const wrap = selId ? h("div", { class: "split" }) : h("div");
  const card = h("div", { class: "card" });
  if (!rows.length) card.append(h("div", { class: "empty",
    text: all.length ? "No trials match these filters."
      : (st.status === null ? "loading…" : "No trials on this ledger yet — nothing has run.") }));
  else {
    const table = h("table");
    const currency = ((st.status && st.status.stages) || {}).spend || {};
    const sortRaw = p.get("sort") || "";
    /* visible-row aggregates ride the column headers [operator-ui §Compare
       precedent]: the numbers describe exactly the rows below them */
    const agg = { pass: 0, fail: 0, cant: 0, pending: 0, cost: 0, costMeasured: 0, unmeasured: 0 };
    for (const t of rows) {
      if (t.graded === "pass") agg.pass += 1; else if (t.graded === "fail") agg.fail += 1;
      else if (t.graded === "cant_grade") agg.cant += 1; else agg.pending += 1;
      if (t.cost === null || t.cost === undefined) agg.unmeasured += 1;
      else { agg.cost += t.cost; agg.costMeasured += 1; }
      if (t.wall === null || t.wall === undefined) agg.unmeasured += 1;
    }
    /* A/B identity from the spec's arm order — the same mapping the verdict
       card and compare use, so blue/orange mean the same thing everywhere */
    const arms = (((st.status || {}).stages || {}).spec || {}).arms || [];
    const armDot = (arm) => {
      const i = arms.indexOf(arm);
      if (i !== 0 && i !== 1) return null;
      return h("span", { class: "armdot", style: "background: var(" + (i === 0 ? "--arm-a" : "--arm-b") + ")" });
    };
    const headRow = h("tr", {});
    /* the work comes first: task/arm/outcome lead, the opaque id sits last */
    const heads = [["task", "task"], ["arm", "arm"], ["rep", "rep"], ["outcome", "outcome"],
                   ["grade", "grade"], ["cost", "cost" + (currency.currency ? " (" + currency.currency + ")" : "")],
                   ["wall", "wall (s)"]];
    for (const [field, label] of heads) {
      const mark = sortRaw === field ? " ▲" : (sortRaw === "-" + field ? " ▼" : "");
      const th = h("th", { class: "sortable", tabindex: "0", text: label + mark,
        title: "sort · state lives in the URL" });
      /* cycle: none -> desc -> asc -> none */
      th.addEventListener("click", () => setParam("sort",
        sortRaw === "-" + field ? field : (sortRaw === field ? null : "-" + field)));
      if (field === "grade") {
        const bits = [agg.pass + " pass", agg.fail + " fail"];
        if (agg.cant) bits.push(agg.cant + " can't");
        if (agg.pending) bits.push(agg.pending + " pending");
        th.append(h("div", { class: "thsub", text: bits.join(" · ") }));
      }
      if (field === "cost" && agg.costMeasured)
        th.append(h("div", { class: "thsub", text: fmt(agg.cost) + " total" }));
      headRow.append(th);
    }
    headRow.append(h("th", { text: "flags" }));
    headRow.append(h("th", { text: "trial" }));
    table.append(headRow);
    rows.forEach((t, i) => {
      const tr = h("tr", { class: "row" + (i === S.sel || t.trial_id === selId ? " sel" : "") });
      tr.addEventListener("click", () => setParam("sel", t.trial_id));
      const armTd = h("td", {});
      const dot = armDot(t.arm);
      if (dot) armTd.append(dot);
      armTd.append(t.arm);
      tr.append(
        h("td", {}, h("b", { text: t.task_id })),
        armTd,
        h("td", { class: "mono", text: String(t.repetition) }),
        h("td", {}, h("span", { class: "chip" + (t.outcome === "completed" ? "" : " warn"), text: t.outcome })),
        h("td", {}, gradeChip(t)),
        h("td", { class: "mono", title: t.cost === null || t.cost === undefined ? "not measured" : "",
          text: t.cost === null || t.cost === undefined ? "—" : fmt(t.cost) }),
        h("td", { class: "mono", title: t.wall === null || t.wall === undefined ? "not measured" : "",
          text: t.wall === null || t.wall === undefined ? "—" : fmt(t.wall) }),
        h("td", {}, ...(t.flagged ? [h("span", { class: "chip bad click", tabindex: "0", text: "⚑ flag",
                     onclick: (e) => { e.stopPropagation();  /* deep link, not row select [EVAL-19 AC-5] */
                       nav("#/exp/" + encodeURIComponent(S.route.exp) + "/trial/" + encodeURIComponent(t.trial_id) + "?tab=forensics"); } })] : []),
                   ...(t.quarantined ? [h("span", { class: "chip bad", text: "quarantined" })] : []),
                   ...(t.egress ? [h("span", { class: "chip warn", text: "egress " + t.egress })] : [])),
        h("td", { class: "mono dim3", title: t.trial_id, text: t.trial_id.slice(0, 16) + "…" }));
      table.append(tr);
    });
    card.append(table);
    /* absence gets ONE footnote, not a phrase per cell [operator-ui §Everywhere] */
    if (agg.unmeasured) card.append(h("div", { class: "dim3", style: "margin-top:6px", text: "— = not measured" }));
  }
  wrap.append(card);
  if (selId) {
    const panel = h("div", { class: "panel" });
    const t = all.find(x => x.trial_id === selId);
    panel.append(h("h2", { text: selId.slice(0, 22) }));
    if (!t) panel.append(h("div", { class: "dim3", text: "not on this ledger" }));
    else {
      panel.append(h("div", {}, h("b", { text: t.task_id + " / " + t.arm + " · rep " + t.repetition })));
      const marks = h("div", { style: "margin:6px 0 2px; display:flex; gap:6px; flex-wrap:wrap" });
      marks.append(h("span", { class: "chip" + (t.outcome === "completed" ? "" : " warn"), text: t.outcome }), gradeChip(t));
      if (t.flagged) marks.append(h("span", { class: "chip bad", text: "⚑ flagged" }));
      if (t.quarantined) marks.append(h("span", { class: "chip bad", text: "quarantined" }));
      if (t.egress) marks.append(h("span", { class: "chip warn", text: "egress " + t.egress }));
      panel.append(marks);
      panel.append(h("div", { class: "dim mono", title: "— = not measured",
        text: "cost " + (t.cost === null || t.cost === undefined ? "—" : fmt(t.cost))
          + " · wall " + (t.wall === null || t.wall === undefined ? "—" : fmt(t.wall)) }));
      /* the pair's advisory verdict, when the judge has spoken on this cell */
      const w = pairVerdict(st, t.task_id, t.repetition);
      if (w) panel.append(h("div", { class: "dim", style: "margin-top:4px",
        text: "judge on this pair: " + w + " (ADVISORY)" }));
      panel.append(h("div", { style: "margin-top:10px; display:flex; gap:6px; flex-wrap:wrap" },
        h("button", { class: "btn", text: "Open full trial ↦ (enter)",
          onclick: () => nav("#/exp/" + encodeURIComponent(S.route.exp) + "/trial/" + encodeURIComponent(selId)) }),
        h("button", { class: "btn", text: "Compare this pair →",
          onclick: () => nav("#/exp/" + encodeURIComponent(S.route.exp) + "/compare") })));
    }
    wrap.append(panel);
  }
  app.append(wrap);
}
/* the advisory winner recorded for one (task, repetition) cell, if any */
function pairVerdict(st, taskId, rep) {
  for (const ev of st.events) {
    if (ev.event !== "judge_verdict") continue;
    const v = ev.verdict || {};
    if (v.comparison_id === "cmp-" + taskId + "-r" + rep) return v.winner || null;
  }
  return null;
}

/* agent lanes [operator-ui §Trial process]: first-seen order over reasoning and
   steps; six categorical slots, then muted ink. The agent NAME always rides
   the color (relief rule) — color alone never identifies an agent. A trial
   with no attribution anywhere gets no lane machinery at all: the page never
   invents a "main agent" the record did not declare. */
const LANE_VARS = ["var(--lane-1)", "var(--lane-2)", "var(--lane-3)",
                   "var(--lane-4)", "var(--lane-5)", "var(--lane-6)"];
function laneModel(d) {
  const steps = ((d.trajectory || {}).steps) || [];
  const entries = ((d.flight_recorder || {}).entries) || [];
  const order = [], totals = {};
  const see = (agent) => {
    const key = agent || "unattributed";
    if (!(key in totals)) { totals[key] = { rows: 0, tokens: 0, measured: false }; order.push(key); }
    return totals[key];
  };
  const count = (t, tokens) => { t.rows += 1;
    if (tokens !== null && tokens !== undefined) { t.tokens += tokens; t.measured = true; } };
  for (const e of entries) count(see(e.agent), e.tokens);
  for (const s of steps) count(see(s.agent), s.tokens);
  const color = {};
  order.forEach((a, i) => { color[a] = LANE_VARS[i] || "var(--ink-3)"; });
  let maxTok = 0;
  for (const e of entries) if (e.tokens) maxTok = Math.max(maxTok, e.tokens);
  for (const s of steps) if (s.tokens) maxTok = Math.max(maxTok, s.tokens);
  /* lanes are real only when the record attributes at least one agent */
  const lanes = order.length > 1 || (order.length === 1 && order[0] !== "unattributed");
  return { order, totals, color, maxTok, lanes };
}
/* the measured-usage cell: a bar scaled to the trial max plus the exact
   count. No tokens = no bar and no number — unmeasured is never zero. */
function tokCell(tokens, color, maxTok) {
  const cell = h("span", { class: "tokcell" });
  if (tokens === null || tokens === undefined) return cell;
  if (maxTok > 0) {
    const track = h("span", { class: "track" });
    track.append(h("span", { class: "fill", style: "display:block; background:" + (color || "var(--ink-3)")
      + "; width:" + Math.max(3, Math.round(100 * tokens / maxTok)) + "%" }));
    cell.append(track);
  }
  cell.append(h("span", { class: "num", text: fmt(tokens, 0) + " tok" }));
  return cell;
}
function laneProps(agent, ctx) {
  const key = agent || "unattributed";
  const cls = (ctx && ctx.lanes ? " lane" : "")
    + (ctx && ctx.filter && key !== ctx.filter ? " dimrow" : "");
  const style = ctx && ctx.lanes && agent ? "border-left-color:" + ctx.color[key] : "";
  return { cls, style };
}
/* short operator glyphs for step kinds; unknown kinds show themselves */
const KIND_GLYPH = { file_edit: "edit", test_run: "run", message: "msg" };
/* one trajectory step as a timeline row — shared by the trajectory tab and
   the process view, so an action renders identically in both */
function stepLi(s, ctx) {
  const lane = laneProps(s.agent, ctx);
  const headline = s.command ? s.command : (s.files_touched || []).join(", ");
  const li = h("li", { class: lane.cls.trim(), style: lane.style },
    h("span", { class: "t", text: s.relative_ts === null || s.relative_ts === undefined ? "—" : fmt(s.relative_ts, 0) + "s" }),
    h("span", { class: "ico", title: s.kind, text: KIND_GLYPH[s.kind] || s.kind }));
  /* multi-agent attribution [EVAL-21]: name the sub-agent when the
     record carries it; single-agent/pre-v3 records stay unlabeled */
  if (s.agent) li.append(h("span", { class: "agent", text: s.agent }));
  // v3 step content [EVAL-14-D004]: shown when captured; a null detail
  // on a contentless step is the honest pre-v3 / not-exposed state
  const body = h("div", { style: "min-width:0; flex:1" });
  if (headline) body.append(h("div", { class: "mono dim", text: headline }));
  if (s.detail !== null && s.detail !== undefined && s.detail !== "" && s.detail !== headline)
    body.append(h("pre", { class: "code", style: "margin-top:2px", text: s.detail }));
  if (!headline && (s.detail === null || s.detail === undefined))
    body.append(h("div", { class: "mono dim3", text: "(not captured in this record version)" }));
  li.append(body);
  if (s.exit_code !== null && s.exit_code !== undefined)
    li.append(h("span", { class: "dim3", text: "exit " + s.exit_code }));
  li.append(tokCell(s.tokens, ctx && s.agent ? ctx.color[s.agent] : null, ctx ? ctx.maxTok : 0));
  return li;
}
/* one reasoning span as a timeline row — visually a THOUGHT, not an action.
   Long bodies clamp to six lines; "show all" expands in place and the
   expanded set rides the URL (?th=idx,idx), surviving the poll re-render. */
function thoughtLi(e, ctx, clampKey) {
  const lane = laneProps(e.agent, ctx);
  const li = h("li", { class: ("thought " + lane.cls).trim(), style: lane.style },
    h("span", { class: "t", text: e.relative_ts === null || e.relative_ts === undefined ? "—" : fmt(e.relative_ts, 0) + "s" }),
    h("span", { class: "ico think", text: "thought" }));
  if (e.agent) li.append(h("span", { class: "agent", text: e.agent }));
  const body = h("div", { style: "min-width:0; flex:1" });
  const long = clampKey !== undefined && String(e.content || "").length > 420;
  const thOpen = new Set((S.route.params.get("th") || "").split(",").filter(Boolean));
  const open = long && thOpen.has(String(clampKey));
  body.append(h("div", { class: "reasonrow" + (long && !open ? " clamp" : ""), text: e.content }));
  if (long) body.append(h("button", { class: "showall", type: "button",
    text: open ? "collapse" : "show all",
    onclick: () => {
      const set = new Set((S.route.params.get("th") || "").split(",").filter(Boolean));
      if (open) set.delete(String(clampKey)); else set.add(String(clampKey));
      setParam("th", [...set].join(",") || null);
    } }));
  li.append(body, tokCell(e.tokens, ctx && e.agent ? ctx.color[e.agent] : null, ctx ? ctx.maxTok : 0));
  return li;
}
/* The unified process view [flight-recorder charter]: ONE timeline tracing the
   stack's thought AND action to the solution. Interleaving uses only what the
   record DECLARES — a reasoning entry with a v3 ``turn`` renders before its
   step (thought precedes action); one with only ``relative_ts`` merges by the
   shared trial clock; one with neither is listed unlinked in capture order.
   Nothing is inferred, reordered by guesswork, or dropped. Filtering an agent
   DIMS the other lanes, never removes them — the timeline stays whole. */
function renderProcess(card, d) {
  const steps = d.trajectory.steps;
  const fr = d.flight_recorder || {};
  const entries = fr.entries || [];
  if (fr.status && fr.status !== "verified" && fr.status !== "absent")
    card.append(h("div", { class: "gerr", style: "margin-bottom:8px",
      text: "flight recorder " + fr.status + " — reasoning withheld (its bytes must match the ledgered sha)" }));
  if (steps === null && !entries.length) {
    card.append(h("div", { class: "empty", text: "trajectory " + d.trajectory.status + (fr.status === "absent" ? " · no reasoning captured" : "") + " — no process to show" }));
    return;
  }
  const lanes = laneModel(d);
  const filter = S.route.params.get("agent") || null;
  const ctx = { color: lanes.color, maxTok: lanes.maxTok, lanes: lanes.lanes,
                filter: lanes.lanes && lanes.order.indexOf(filter) >= 0 ? filter : null };
  if (lanes.lanes) {
    /* the cast of agents, first-seen order, with per-agent totals: a legend
       that filters. Totals show only what was measured. */
    const bar = h("div", { class: "agentbar" });
    bar.append(h("span", { class: "eyebrow", text: "agents" }));
    for (const a of lanes.order) {
      const t = lanes.totals[a];
      bar.append(h("button", { type: "button", class: "agentchip" + (ctx.filter === a ? " on" : ""),
        title: ctx.filter === a ? "showing " + a + " at full strength — click to reset"
                                : "dim the other agents' rows (the timeline stays whole)",
        onclick: () => setParam("agent", ctx.filter === a ? null : a) },
        h("span", { class: "dot", style: "background:" + lanes.color[a] }), a,
        h("span", { class: "sub", text: t.rows + (t.measured ? " · " + fmt(t.tokens, 0) + " tok" : "") })));
    }
    if (filter && lanes.order.indexOf(filter) < 0)
      bar.append(h("span", { class: "gerr", text: "unknown agent '" + filter + "' — ignored" }));
    card.append(bar);
  }
  card.append(h("div", { class: "dim3", style: "margin-bottom:8px",
    text: "one timeline — thought (flight recorder) interleaved with action (trajectory), both sha-verified; placement is the stack's own declared linkage, never inferred" }));
  /* capture-order index rides each entry: it keys the clamp state and never
     changes when the interleave regroups rows */
  const wrapped = entries.map((e, idx) => ({ e, idx }));
  const byTurn = {}, tsOnly = [], unlinked = [];
  for (const w of wrapped) {
    const e = w.e;
    const hasTurn = e.turn !== null && e.turn !== undefined;
    if (hasTurn && steps !== null && e.turn < steps.length)
      (byTurn[e.turn] = byTurn[e.turn] || []).push(w);
    else if (hasTurn)
      /* a DECLARED link whose step is unavailable is a broken link to state,
         never a license to re-place the thought by some other signal */
      unlinked.push({ w, note: "declared turn " + e.turn + " — step unavailable" });
    else if (e.relative_ts !== null && e.relative_ts !== undefined) tsOnly.push(w);
    else unlinked.push({ w, note: null });
  }
  tsOnly.sort((a, b) => a.e.relative_ts - b.e.relative_ts);  /* stable: capture order breaks ties */
  const ul = h("ul", { class: "steps" });
  let cursor = 0;
  const flushTs = (limit) => {
    while (cursor < tsOnly.length && (limit === null || tsOnly[cursor].e.relative_ts <= limit)) {
      ul.append(thoughtLi(tsOnly[cursor].e, ctx, tsOnly[cursor].idx)); cursor += 1;
    }
  };
  (steps || []).forEach((s, i) => {
    if (s.relative_ts !== null && s.relative_ts !== undefined) flushTs(s.relative_ts);
    for (const w of byTurn[i] || []) ul.append(thoughtLi(w.e, ctx, w.idx));  /* thought precedes its action */
    ul.append(stepLi(s, ctx));
  });
  flushTs(null);
  card.append(ul);
  if (unlinked.length) {
    card.append(h("h2", { style: "margin-top:12px", text: "Unlinked reasoning (capture order)" }));
    card.append(h("div", { class: "dim3", style: "margin-bottom:4px",
      text: "spans whose position in the timeline is not declared — they belong to this trial, placement unknown (honest, not hidden)" }));
    const ul2 = h("ul", { class: "steps" });
    for (const u of unlinked) {
      const li = thoughtLi(u.w.e, ctx, u.w.idx);
      if (u.note) li.append(h("span", { class: "gerr", text: " " + u.note }));
      ul2.append(li);
    }
    card.append(ul2);
  }
  if (!entries.length && fr.status === "absent")
    card.append(h("div", { class: "dim3", style: "margin-top:8px", text: "no reasoning captured for this trial — the timeline above is action only" }));
}

function renderTrial(app) {
  const st = expState(S.route.exp);
  const d = st.trial[S.route.id];
  if (!d) { app.append(withheldCard(st) || h("div", { class: "empty", text: "loading trial…" })); return; }
  const rec = d.record || {};
  const head = h("div", { class: "toolbar card" });
  head.append(h("b", { class: "mono", text: d.trial_id }),
    h("span", { class: "chip", text: rec.task_id + " / " + rec.arm + " · rep " + rec.repetition }),
    h("span", { class: "chip" + (rec.outcome === "completed" ? "" : " warn"), text: rec.outcome }),
    h("span", { class: "chip" + (d.trajectory.status === "verified" ? " ok" : ""), text: "trajectory " + d.trajectory.status }));
  if (d.quarantine) head.append(h("span", { class: "chip bad", text: "quarantined: " + d.quarantine.reason }));
  head.append(h("span", { class: "spacer" }),
    h("button", { class: "btn", text: "compare →", onclick: () => nav("#/exp/" + encodeURIComponent(S.route.exp) + "/compare") }),
    h("button", { class: "btn", text: "← trials", onclick: () => nav("#/exp/" + encodeURIComponent(S.route.exp) + "/trials") }));
  /* what this trial cost and how long it ran — the record's telemetry, with
     absence as a compact "—" (one tooltip explains it), never zeroed and
     never a sentence of "not measured"s [EVAL-4-D004, operator-ui §Trial process] */
  const tel = rec.telemetry || {};
  const telVal = (v, f) => (v === null || v === undefined) ? "—" : f(v);
  const telLine = "cost " + telVal(tel.cost, (x) => fmt(x))
    + " · wall " + telVal(tel.wall_time_s, fmtDur)
    + " · tokens in " + telVal(tel.tokens_in, (x) => fmt(x, 0))
    + " / out " + telVal(tel.tokens_out, (x) => fmt(x, 0))
    + " · tool calls " + telVal(tel.tool_calls, (x) => fmt(x, 0));
  head.append(h("div", { class: "dim3 mono", style: "flex-basis:100%",
    title: "— = not measured", text: telLine }));
  /* the advisory verdict on this trial's pair, surfaced where the operator
     is looking — the data was always in the payload */
  for (const v of d.verdicts || [])
    head.append(h("div", { class: "dim", style: "flex-basis:100%",
      text: "judge on this pair (ADVISORY): " + (v.winner || "?") + (v.reason ? " — " + v.reason : "") }));
  app.append(head);

  const tab = S.route.params.get("tab") || "trajectory";
  const tabs = h("div", { class: "toolbar" });
  for (const name of ["trajectory", "process", "grade", "forensics", "egress", "raw"])
    tabs.append(h("span", { class: "chip click" + (tab === name ? " on" : ""), tabindex: "0", text: name, onclick: () => setParam("tab", name) }));
  app.append(tabs);

  const card = h("div", { class: "card" });
  if (tab === "trajectory") {
    const steps = d.trajectory.steps;
    if (steps === null) card.append(h("div", { class: "empty", text: "trajectory " + d.trajectory.status + " — steps unavailable (status is data, not an error)" }));
    else if (!steps.length) card.append(h("div", { class: "empty", text: "trajectory verified, zero steps" }));
    else {
      const ul = h("ul", { class: "steps" });
      const lanes = laneModel(d);
      const ctx = { color: lanes.color, maxTok: lanes.maxTok, lanes: lanes.lanes, filter: null };
      for (const s of steps) ul.append(stepLi(s, ctx));
      card.append(ul);
    }
  } else if (tab === "process") {
    renderProcess(card, d);
  } else if (tab === "grade") {
    if (!d.grade.grades.length && !d.grade.cant_grades.length) card.append(h("div", { class: "empty", text: "not graded yet" }));
    for (const g of d.grade.grades) {
      card.append(h("div", {}, h("b", { text: "grade: " + (g.binary_score ? "pass" : "fail") }),
        h("span", { class: "dim3", text: "  " + (g.grader || "") + (g.override_of ? " · override" : "") })));
      const table = h("table");
      table.append(h("tr", {}, h("th", { text: "assertion" }), h("th", { text: "source" }), h("th", { text: "result" })));
      for (const a of g.assertions || [])
        table.append(h("tr", {}, h("td", { class: "mono", text: a.id || "" }), h("td", { class: "dim", text: a.source || "" }),
          h("td", {}, h("span", { class: "chip " + (a.result === "pass" ? "ok" : "bad"), text: a.result || "" }))));
      card.append(table);
    }
    for (const c of d.grade.cant_grades)
      card.append(h("div", { class: "dim", text: "cant_grade → " + c.reason + (c.override_of ? " (override attempt)" : "") }));
  } else if (tab === "forensics") {
    if (!d.forensics.flags.length && !d.forensics.metrics) {
      /* three honest states, from the ledger the page already holds:
         never scanned / scanned but this trial is a coverage gap / scanned
         clean — "no scan yet" must not describe a clean pass */
      const reports = st.events.filter(e => e.event === "forensics_report");
      if (!reports.length)
        card.append(h("div", { class: "empty", text: "no forensics scan on this ledger yet" }));
      else {
        const cov = ((reports[reports.length - 1].forensics_report || {}).coverage || {});
        const gap = (cov.gaps || []).indexOf(d.trial_id) >= 0;
        card.append(h("div", { class: "empty", text: gap
          ? "⚠ the latest scan did NOT cover this trial (coverage gap)"
          : "covered by the latest scan — no flags, no metrics recorded for this trial" }));
      }
    }
    for (const f of d.forensics.flags)
      card.append(h("div", {}, h("span", { class: "chip bad", text: f.detector || "flag" }), h("span", { class: "dim", text: " " + (f.reason || "") + " — evidence, never a verdict" })));
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

function shortModel(m) { return m ? m.split("/").pop().replace(/-\d{8}$/, "") : ""; }

function armHead(c) {
  // label the two-column layout so it's unambiguous which side is arm A vs B,
  // and name the model each arm actually ran; the identity dot rides every
  // arm label so the color legend travels with the name [operator-ui §Tokens]
  return h("div", { class: "armhead" },
    h("div", {}, h("span", { class: "armdot", style: "background: var(--arm-a)" }),
      "A · " + c.arm_a, h("span", { class: "amodel", text: shortModel(c.arm_a_model) })),
    h("div", {}, h("span", { class: "armdot", style: "background: var(--arm-b)" }),
      "B · " + c.arm_b, h("span", { class: "amodel", text: shortModel(c.arm_b_model) })));
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
    for (const e of groups[role]) {
      col.append(h("div", { class: "reason", text: e.content }));
      /* measured usage beside the turn — a metered model turn is legible
         against an unmeasured one (deterministic orchestrator step, v1
         recorder). Null renders as NOTHING: unmeasured is never zero, and
         the page never infers "code-authored" from absence. */
      const bits = [];
      if (e.tokens !== null && e.tokens !== undefined) bits.push(fmt(e.tokens, 0) + " tok");
      if (e.cost !== null && e.cost !== undefined) bits.push("cost " + fmt(e.cost, 4));
      if (bits.length) col.append(h("div", { class: "rmeta", text: bits.join(" · ") }));
    }
  }
  return col;
}

function renderCompare(app) {
  const st = expState(S.route.exp);
  const gate = withheldCard(st);
  if (gate) { app.append(gate); return; }
  const c = st.compare;
  if (!c) { app.append(h("div", { class: "empty", text: "assembling comparisons…" })); return; }
  if (c.error) { app.append(h("div", { class: "card", text: c.error })); return; }
  const only = S.route.params.get("only") === "disagreements";
  const head = h("div", { class: "toolbar card" });
  const title = h("b", {});
  title.append("Compare — ", h("span", { class: "armdot", style: "background: var(--arm-a)" }),
    "A · " + c.arm_a, h("span", { class: "dim3", text: " " + shortModel(c.arm_a_model) + "  " }),
    " vs  ", h("span", { class: "armdot", style: "background: var(--arm-b)" }),
    "B · " + c.arm_b, h("span", { class: "dim3", text: " " + shortModel(c.arm_b_model) }));
  head.append(title,
    h("span", { class: "chip" + (c.official_ready ? " ok" : ""), text: c.official_ready ? "official fence PASSES" : "EXPLORATORY — official fence not passed" }));
  head.append(h("span", { class: "spacer" }),
    h("span", { class: "chip click" + (only ? " on" : ""), tabindex: "0", text: "only disagreements (" + c.summary.disagreements + ")" + (only ? " ✕" : ""),
      onclick: () => setParam("only", only ? null : "disagreements") }));
  app.append(head);
  { // the same verdict the overview leads with, compact: counts wear the arm
    // dots, the lead is weight not color, the judge stays one advisory line
    const H = c.summary.holdout, J = c.summary.judge;
    const pairs = H.a_only + H.b_only + H.both + H.neither;
    const ungraded = c.summary.pairs - pairs;
    const aPass = H.a_only + H.both, bPass = H.b_only + H.both;
    const holdoutOut = aPass > bPass ? "→ " + c.arm_a + " leads" : (bPass > aPass ? "→ " + c.arm_b + " leads" : "→ tie");
    const judgeLean = J.b > J.a ? "→ leans " + c.arm_b : (J.a > J.b ? "→ leans " + c.arm_a : "→ tie/mixed");
    const banner = h("div", { class: "card verdict" });
    banner.append(h("div", { class: "eyebrow", text: "Result · holdout (deterministic) · " + pairs + " graded pair(s)" }));
    const l1 = h("div", { style: "margin-top:4px" },
      h("span", { class: "armdot", style: "background: var(--arm-a)" }), c.arm_a + " ",
      h("b", { class: "mono", text: String(aPass) }),
      h("span", { class: "dim3", text: "/" + pairs + "   " }),
      h("span", { class: "armdot", style: "background: var(--arm-b)" }), c.arm_b + " ",
      h("b", { class: "mono", text: String(bPass) }),
      h("span", { class: "dim3", text: "/" + pairs + "   " }),
      h("b", { text: holdoutOut }));
    /* honest denominator: pairs the tallies exclude are named, not hidden */
    if (ungraded > 0) l1.append(h("span", { class: "dim3", text: "  · " + ungraded + " of " + c.summary.pairs + " pair(s) not fully graded, excluded from these tallies" }));
    banner.append(l1,
      h("div", { class: "verdict-judge" }, h("span", { class: "eyebrow", text: "Judge" }),
        " " + c.arm_a + " " + J.a + " · " + c.arm_b + " " + J.b + " · tie " + J.tie + " ",
        h("b", { text: judgeLean }),
        h("span", { class: "dim3", text: " — advisory, not an official verdict" })));
    app.append(banner);
  }
  /* the signature tape, on the screen its cells open into: a click expands
     that pair's row in place (the open set rides the URL) and scrolls to it */
  const tapeEl = pairTape(st, { onCell: (cell) => {
    const token = encodeURIComponent("cmp-" + cell.task + "-r" + cell.rep);
    const set = new Set((S.route.params.get("open") || "").split(",").filter(Boolean));
    set.add(token);
    setParam("open", [...set].join(","));
    /* hashchange dispatch is async — scroll once the re-render has landed */
    setTimeout(() => {
      const el = document.getElementById("pair-cmp-" + cell.task + "-r" + cell.rep);
      if (el) el.scrollIntoView();
    }, 60);
  } });
  if (tapeEl) {
    const tapeCard = h("div", { class: "card" });
    tapeCard.append(h("div", { class: "eyebrow", style: "margin-bottom:4px", text: "Pair tape — a cell opens its pair below" }), tapeEl);
    app.append(tapeCard);
  }
  /* tallies are navigation [EVAL-19 AC-5]: every count filters to its slice,
     and the slice lives in the URL. Predicates mirror compare.py's summary
     arithmetic exactly, so a tally always equals its filtered row count. The
     chips render as stat chips [operator-ui §Compare]: results first, filters second. */
  const slice = S.route.params.get("slice") || "";
  const knownSlice = ["holdout:a_only", "holdout:b_only", "holdout:both", "holdout:neither",
                      "judge:a", "judge:b", "judge:tie", "judge:cant", "judge:unjudged"];
  const statchip = (key, label, count, why) => h("button", {
    type: "button", class: "statchip" + (slice === key ? " on" : ""),
    title: (why ? why + " — " : "") + (slice === key ? "click to clear the filter" : "click to filter to these pairs"),
    onclick: () => setParam("slice", slice === key ? null : key) },
    h("span", { class: "n", text: String(count) }),
    h("span", { class: "l", text: label }));
  const sm = h("div", { class: "toolbar card" });
  sm.append(h("div", { class: "statgroup" },
    h("span", { class: "eyebrow statlabel", text: "holdout" }),
    statchip("holdout:a_only", c.arm_a + " only", c.summary.holdout.a_only, "pairs where only " + c.arm_a + " passed the holdout"),
    statchip("holdout:b_only", c.arm_b + " only", c.summary.holdout.b_only, "pairs where only " + c.arm_b + " passed the holdout"),
    statchip("holdout:both", "both", c.summary.holdout.both, "pairs where both arms passed"),
    statchip("holdout:neither", "neither", c.summary.holdout.neither, "pairs where both arms failed")));
  sm.append(h("div", { class: "statgroup" },
    h("span", { class: "eyebrow statlabel", text: "judge (advisory)" }),
    statchip("judge:a", c.arm_a, c.summary.judge.a),
    statchip("judge:b", c.arm_b, c.summary.judge.b),
    statchip("judge:tie", "tie", c.summary.judge.tie),
    statchip("judge:cant", "cant judge", c.summary.judge.cant),
    statchip("judge:unjudged", "unjudged", c.summary.judge.unjudged)));
  sm.append(h("span", { class: "spacer" }),
    h("span", { class: "dim3", text: "counts filter · the slice lives in the URL" }));
  if (slice && knownSlice.indexOf(slice) < 0)
    sm.append(h("span", { class: "gerr", text: "unknown slice '" + slice + "' — ignored" }));
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
      : (only ? "No disagreements — arms agree on every pair." : "No complete pairs yet (both arms must have a trial per task/rep).") }));
  /* one table: each row IS its pair — the collapsed scan line — and expands
     in place [operator-ui §Compare, index and diff cards merged]. The open set
     rides the URL (?open=id,id — URI-encoded, comma-joined) like ?fr=, so a
     poll re-render never slams a diff shut and a shared link reproduces it. */
  const openSet = new Set((S.route.params.get("open") || "").split(",").filter(Boolean));
  const frOpen = new Set((S.route.params.get("fr") || "").split(",").filter(Boolean));
  /* a shared ?fr= link must reproduce the open recorder [AC-3]: an fr token
     implies its pair row is open, so the deep link renders on its own */
  for (const t of frOpen) openSet.add(t);
  const setOpen = (set) => setParam("open", [...set].join(",") || null);
  if (pairs.length) {
    const card = h("div", { class: "card" });
    const bar = h("div", { class: "toolbar" });
    bar.append(h("h2", { style: "margin:0", text: "Pairs — " + pairs.length + " of " + c.pairs.length + " shown" }),
      h("span", { class: "spacer" }),
      h("button", { class: "btn", text: "expand all", onclick: () =>
        setOpen(new Set(pairs.map(p => encodeURIComponent(p.comparison_id)))) }),
      h("button", { class: "btn", text: "collapse all", onclick: () => {
        setParam("fr", null);  /* fr implies open — a stale token would reopen its row */
        setOpen(new Set());
      } }));
    card.append(bar);
    const table = h("table");
    /* aggregates live in the column headers [operator-ui §Compare]; the same
       summary numbers the banner shows, beside the column they summarize */
    const Ht = c.summary.holdout, J = c.summary.judge;
    const gradedPairs = Ht.a_only + Ht.b_only + Ht.both + Ht.neither;
    const thArm = (dotVar, name, pass) => {
      const th = h("th", {});
      th.append(h("span", { class: "armdot", style: "background: var(" + dotVar + ")" }), name,
        h("div", { class: "thsub", text: pass + "/" + gradedPairs + " pass" }));
      return th;
    };
    const thJudge = h("th", { text: "judge (ADVISORY)" });
    thJudge.append(h("div", { class: "thsub", text: J.a + " · " + J.b + " · tie " + J.tie }));
    table.append(h("tr", {}, h("th", { text: "task · rep" }),
      thArm("--arm-a", c.arm_a + " (A)", Ht.a_only + Ht.both),
      thArm("--arm-b", c.arm_b + " (B)", Ht.b_only + Ht.both),
      thJudge, h("th", { text: "" }), h("th", { text: "" })));
    const holdoutMark = (v) => v === null || v === undefined
      ? h("span", { class: "dim3", text: "ungraded" })
      : h("span", { class: v ? "chip ok" : "chip bad", text: v ? "✓ pass" : "✕ fail" });
    for (const p of pairs) {
      const token = encodeURIComponent(p.comparison_id);
      const open = openSet.has(token);
      const a = p.a.holdout_pass, b = p.b.holdout_pass;
      /* the row wash names the arm that alone passed; agreement stays quiet */
      const wash = (!!a && !b) ? " washa" : ((!!b && !a) ? " washb" : "");
      const tr = h("tr", { class: "row pairjump" + wash, id: "pair-" + p.comparison_id });
      tr.addEventListener("click", () => {
        const set = new Set(openSet);
        if (open) {
          set.delete(token);
          /* closing the pair closes its recorder too: fr implies open, so a
             stale fr token would reopen this row on the next render */
          const fr = new Set((S.route.params.get("fr") || "").split(",").filter(Boolean));
          if (fr.has(token)) { fr.delete(token); setParam("fr", [...fr].join(",") || null); }
        } else set.add(token);
        setOpen(set);
      });
      const jw = p.judge ? p.judge.winner : null;
      const jname = jw === "A" ? "A · " + c.arm_a : (jw === "B" ? "B · " + c.arm_b : jw);
      tr.append(h("td", {}, h("b", { text: p.task_id }), h("span", { class: "dim3", text: " · rep " + p.repetition })),
        h("td", {}, holdoutMark(a)),
        h("td", {}, holdoutMark(b)),
        h("td", { class: jw ? "" : "dim3", text: jw ? jname : "unjudged" }),
        h("td", {}, ...(p.disagreement ? [h("span", { class: "chip warn", title: "the deterministic grades and/or the advisory judge point different ways", text: "disagreement" })] : [])),
        h("td", { class: "chevcell", text: open ? "▾" : "▸" }));
      table.append(tr);
      if (open) table.append(pairBodyRow(c, p, frOpen));
    }
    card.append(table);
    app.append(card);
  }
}
/* the expanded pair: judge verdict + quoted reason first, then the workspace
   diff, then the flight-recorder drawer — the "why" sits one glance from the
   verdict [operator-ui §Compare]. Rendered as a full-width row under its pair. */
function pairBodyRow(c, p, frOpen) {
  const body = h("div", { class: "pairbody" });
  if (p.judge) {
    const w = p.judge.winner;
    body.append(h("div", {}, h("span", { class: "chip", text: "judge: " + (w === "A" ? "A · " + c.arm_a : (w === "B" ? "B · " + c.arm_b : w)) + " (ADVISORY)" })));
    if (p.judge.reason) body.append(h("div", { class: "quote", text: p.judge.reason }));
  }
  body.append(armHead(c));
  body.append(h("div", { class: "dim3 difflegend", text: "diff — red: only in A/" + c.arm_a + " · green: only in B/" + c.arm_b + " · unhighlighted: identical in both" }));
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
  body.append(diff);
  if ((p.a.reasoning && p.a.reasoning.length) || (p.b.reasoning && p.b.reasoning.length)) {
    /* collapsed by default: the reasoning is a drill-down, not the scan
       path; the content stays in the DOM either way. Open/closed rides the
       URL, so the click survives the poll re-render and a shared link
       reproduces the open panels [AC-3]. */
    const na = (p.a.reasoning || []).length, nb = (p.b.reasoning || []).length;
    const token = encodeURIComponent(p.comparison_id);
    const fr = h("details", { class: "fr" });
    if (frOpen.has(token)) fr.setAttribute("open", "");
    fr.append(h("summary", {
      text: "Flight recorder · reasoning — A " + na + " · B " + nb + " entr(ies) (operator-tier, unblinded)",
      onclick: (e) => {
        e.preventDefault();  /* no native toggle: the URL round-trip renders it */
        const set = new Set((S.route.params.get("fr") || "").split(",").filter(Boolean));
        if (set.has(token)) set.delete(token); else set.add(token);
        setParam("fr", [...set].join(",") || null);
      } }));
    fr.append(armHead(c));
    const rz = h("div", { class: "diff2 rz" });
    rz.append(reasoningCol(p.a.reasoning), reasoningCol(p.b.reasoning));
    fr.append(rz);
    body.append(fr);
  }
  const td = h("td", { colspan: "6" });
  td.append(body);
  return h("tr", { class: "pairbody-row" }, td);
}

function renderFindings(app) {
  const st = expState(S.route.exp);
  const gate = withheldCard(st);
  if (gate) { app.append(gate); return; }
  const f = st.fence;
  if (!f) { app.append(h("div", { class: "empty", text: "checking the fence…" })); return; }
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Official fence" }),
    h("div", { style: "margin-bottom:8px" }, h("span", { class: "chip" + (f.official_ready ? " ok" : ""),
      text: f.official_ready ? "✓ official render available" : "official render refused → exploratory only" })));
  const ul = h("ul", { class: "fence" });
  for (const item of f.items) {
    const mark = { ok: "✓", failed: "✕", unchecked: "○" }[item.state] || "?";
    const cls = { ok: "chip ok", failed: "chip bad", unchecked: "chip" }[item.state];
    ul.append(h("li", {}, h("span", { class: cls, text: mark + " " + item.state }),
      h("b", { text: item.name }), h("span", { class: "dim3", text: item.detail || "" })));
  }
  card.append(ul);
  app.append(card);
  const art = h("div", { class: "card" });
  art.append(h("h2", { text: "Rendered artifacts (read-only)" }));
  if (BUNDLE) {
    art.append(h("div", { class: "dim3", text: "artifacts are not embedded in a bundle — open findings.* beside the experiment's ledger, or use the live observer" }));
  } else {
    const names = ["findings.json", "findings.exploratory.dossier.html", "findings.official.dossier.html",
                   "findings.exploratory.md", "findings.official.md"];
    const bar = h("div", { class: "toolbar" });
    for (const name of names)
      bar.append(h("button", { class: "btn", text: name, onclick: () =>
        window.open("/artifact?exp=" + encodeURIComponent(S.route.exp) + "&name=" + encodeURIComponent(name), "_blank") }));
    art.append(bar);
    /* what the ledger says analyze has rendered, so the operator knows which
       buttons can succeed before clicking */
    const renders = (((st.status || {}).stages || {}).analyze || {}).renders;
    if (renders) art.append(h("div", { class: "dim", style: "margin-top:8px",
      text: "ledgered renders — exploratory: " + fmt(renders.exploratory, 0)
        + " · official: " + fmt(renders.official, 0)
        + (renders.exploratory || renders.official ? "" : " · nothing rendered yet, every button will 404 honestly") }));
    art.append(h("div", { class: "dim3", style: "margin-top:4px",
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
  const lists = { home: () => S.experiments || [], trials: () => visibleTrials(expState(r.exp), r.params) };
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
  " Static bundle: an archived, deterministic render of this experiment's ledger — nothing here is live and nothing can be changed from this page.";
refresh();