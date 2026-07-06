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
  card.append(h("h2", { text: "Experiments" }));
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
        armsTd.append(h("div", { text: sm.arms.join(" vs ") }));
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

/* Client-side paired tallies for the overview strip. Mirrors compare.py's
   summary arithmetic EXACTLY (the slicePred precedent) over the same ledger
   events, so the strip always agrees with the compare screen. */
function pairTallies(st) {
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
  const cells = new Set();
  for (const k of Object.keys(trials)) { const p = k.split("\u0000"); cells.add(p[0] + "\u0000" + p[1]); }
  const t = { a: 0, b: 0, both: 0, neither: 0, graded_pairs: 0, pairs: 0,
              ja: 0, jb: 0, jtie: 0, jcant: 0, junjudged: 0 };
  for (const cell of cells) {
    const [task, rep] = cell.split("\u0000");
    const ta = trials[task + "\u0000" + rep + "\u0000" + armA], tb = trials[task + "\u0000" + rep + "\u0000" + armB];
    if (ta === undefined || tb === undefined) continue;
    t.pairs += 1;
    const a = grades[ta], b = grades[tb];
    if (a !== undefined && b !== undefined && a !== null && b !== null) {
      t.graded_pairs += 1;
      if (a && b) t.both += 1; else if (a) t.a += 1; else if (b) t.b += 1; else t.neither += 1;
    }
    /* "cmp-<task>-r<rep>" mirrors judge.assemble.comparison_id_for */
    const w = winners["cmp-" + task + "-r" + rep];
    if (w === "A") t.ja += 1; else if (w === "B") t.jb += 1;
    else if (w === "TIE") t.jtie += 1; else if (w === "CANT_JUDGE") t.jcant += 1;
    else t.junjudged += 1;
  }
  return { armA, armB, t };
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
  app.append(rail);

  /* result so far: the same deterministic-first summary compare's banner
     shows, one navigation away from the screen an operator lands on */
  const pt = pairTallies(st);
  if (pt && pt.t.pairs) {
    const strip = h("div", { class: "card banner", style: "border-left-color: var(--meter)" });
    const { armA, armB, t } = pt;
    const aPass = t.a + t.both, bPass = t.b + t.both;
    const lead = aPass > bPass ? armA + " leads" : (bPass > aPass ? armB + " leads" : "tie");
    const line1 = h("div", {});
    line1.append(h("span", { class: "dim", text: "Result so far · holdout (deterministic): " }));
    if (t.graded_pairs) {
      line1.append(armA + " " + aPass + "/" + t.graded_pairs + " · " + armB + " " + bPass + "/" + t.graded_pairs + "  ");
      line1.append(h("b", { text: "→ " + lead }));
      if (t.pairs > t.graded_pairs) line1.append(h("span", { class: "dim3", text: "  · " + (t.pairs - t.graded_pairs) + " pair(s) not fully graded yet" }));
    } else line1.append(h("span", { class: "dim3", text: "no fully graded pairs yet" }));
    strip.append(line1);
    const judged = t.ja + t.jb + t.jtie + t.jcant;
    const line2 = h("div", { style: "margin-top:3px" });
    line2.append(h("span", { class: "dim", text: "Judge (ADVISORY): " }));
    if (judged) {
      const jLean = t.jb > t.ja ? "leans " + armB : (t.ja > t.jb ? "leans " + armA : "tie/mixed");
      line2.append(armA + " " + t.ja + " · " + armB + " " + t.jb + " · tie " + t.jtie + "  ", h("b", { text: "→ " + jLean }));
    } else line2.append(h("span", { class: "dim3", text: "no verdicts yet" }));
    line2.append(h("span", { class: "chip click", style: "margin-left:10px", tabindex: "0", text: "open compare →",
      onclick: () => nav("#/exp/" + exp + "/compare") }));
    strip.append(line2);
    app.append(strip);
  }

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

  const feed = h("div", { class: "card" });
  const bar = h("div", { class: "toolbar" });
  bar.append(h("h2", { text: "Ledger feed (newest first)", style: "margin:0" }));
  /* the workhorse kinds get fixed chips; any other kind actually on this
     ledger gets one too, so no event class is only reachable via "all" */
  const kinds = ["trial", "grade", "judge_verdict", "cant_grade", "trial_infra_failed"];
  const extra = [...new Set(st.events.map(e => e.event))]
    .filter(k => k && kinds.indexOf(k) < 0).sort().slice(0, 8);
  const active = S.route.params.get("kind") || "";
  bar.append(h("span", { class: "chip click" + (!active ? " on" : ""), tabindex: "0", text: "all", onclick: () => setParam("kind", null) }));
  for (const k of kinds.concat(extra))
    bar.append(h("span", { class: "chip click" + (active === k ? " on" : ""), tabindex: "0", text: k, onclick: () => setParam("kind", k) }));
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
  for (const ev of shown) {
    const target = feedTarget(ev);
    const li = h("li", { class: target ? "click" : "" },
      h("span", { class: "ts", text: ((ev.provenance || {}).ts || "").replace("T", " ").slice(0, 19) }),
      h("span", { class: "k", text: ev.event || "?" }),
      h("span", { text: summarize(ev) }));
    if (target) li.addEventListener("click", () => nav(target));
    ul.append(li);
  }
  feed.append(ul);
  app.append(feed);
}

function gradeChip(t) {
  const cls = { pass: "chip ok", fail: "chip bad", cant_grade: "chip bad", pending: "chip" }[t.graded];
  return h("span", { class: cls, text: t.graded });
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
  const facetBar = h("div", { class: "toolbar card" });
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
  app.append(facetBar);
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
  /* saved views: stored URL fragments, local to this browser [EVAL-19 AC-3] */
  const viewsBar = h("div", { class: "toolbar card" });
  viewsBar.append(h("h2", { text: "Views", style: "margin:0" }));
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
  app.append(viewsBar);

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
    const headRow = h("tr", {});
    headRow.append(h("th", { text: "trial" }));
    const heads = [["task", "task"], ["arm", "arm"], ["rep", "rep"], ["outcome", "outcome"],
                   ["grade", "grade"], ["cost", "cost" + (currency.currency ? " (" + currency.currency + ")" : "")],
                   ["wall", "wall (s)"]];
    for (const [field, label] of heads) {
      const on = sortRaw === field || sortRaw === "-" + field;
      const mark = sortRaw === field ? " ▲" : (sortRaw === "-" + field ? " ▼" : "");
      const th = h("th", { class: "sortable", tabindex: "0", text: label + mark,
        title: "sort · state lives in the URL" });
      /* cycle: none -> desc -> asc -> none */
      th.addEventListener("click", () => setParam("sort",
        sortRaw === "-" + field ? field : (sortRaw === field ? null : "-" + field)));
      headRow.append(th);
    }
    headRow.append(h("th", { text: "flags" }));
    table.append(headRow);
    rows.forEach((t, i) => {
      const tr = h("tr", { class: "row" + (i === S.sel || t.trial_id === selId ? " sel" : "") });
      tr.addEventListener("click", () => setParam("sel", t.trial_id));
      tr.append(
        h("td", { class: "mono", title: t.trial_id, text: t.trial_id.slice(0, 16) + "…" }),
        h("td", { text: t.task_id }), h("td", { text: t.arm }), h("td", { class: "mono", text: String(t.repetition) }),
        h("td", {}, h("span", { class: "chip" + (t.outcome === "completed" ? "" : " warn"), text: t.outcome })),
        h("td", {}, gradeChip(t)),
        h("td", { class: "mono", text: t.cost === null || t.cost === undefined ? "—" : fmt(t.cost) }),
        h("td", { class: t.wall === null || t.wall === undefined ? "dim3" : "mono", text: nm(t.wall) }),
        h("td", {}, ...(t.flagged ? [h("span", { class: "chip bad click", tabindex: "0", text: "⚑ flag",
                     onclick: (e) => { e.stopPropagation();  /* deep link, not row select [EVAL-19 AC-5] */
                       nav("#/exp/" + encodeURIComponent(S.route.exp) + "/trial/" + encodeURIComponent(t.trial_id) + "?tab=forensics"); } })] : []),
                   ...(t.quarantined ? [h("span", { class: "chip bad", text: "quarantined" })] : []),
                   ...(t.egress ? [h("span", { class: "chip warn", text: "egress " + t.egress })] : [])));
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
      panel.append(h("div", {}, h("b", { text: t.task_id + " / " + t.arm + " · rep " + t.repetition })));
      const marks = h("div", { style: "margin:6px 0 2px; display:flex; gap:6px; flex-wrap:wrap" });
      marks.append(h("span", { class: "chip" + (t.outcome === "completed" ? "" : " warn"), text: t.outcome }), gradeChip(t));
      if (t.flagged) marks.append(h("span", { class: "chip bad", text: "⚑ flagged" }));
      if (t.quarantined) marks.append(h("span", { class: "chip bad", text: "quarantined" }));
      if (t.egress) marks.append(h("span", { class: "chip warn", text: "egress " + t.egress }));
      panel.append(marks);
      panel.append(h("div", { class: "dim", text: "cost " + (t.cost === null || t.cost === undefined ? "—" : fmt(t.cost)) + " · wall " + nm(t.wall) }));
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

/* one trajectory step as a timeline row — shared by the trajectory tab and
   the process view, so an action renders identically in both */
function stepLi(s) {
  const headline = s.command ? s.command : (s.files_touched || []).join(", ");
  const li = h("li", {},
    h("span", { class: "t", text: s.relative_ts === null || s.relative_ts === undefined ? "—" : fmt(s.relative_ts, 0) + "s" }),
    h("span", { class: "ico", text: s.kind }));
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
  li.append(body,
    h("span", { class: "dim3", text: (s.exit_code === null || s.exit_code === undefined ? "" : " exit " + s.exit_code) +
      (s.tokens === null || s.tokens === undefined ? "" : " · " + fmt(s.tokens, 0) + " tok") }));
  return li;
}
/* one reasoning span as a timeline row — visually a THOUGHT, not an action */
function thoughtLi(e) {
  const li = h("li", { class: "thought" },
    h("span", { class: "t", text: e.relative_ts === null || e.relative_ts === undefined ? "—" : fmt(e.relative_ts, 0) + "s" }),
    h("span", { class: "ico think", text: "thought" }));
  if (e.agent) li.append(h("span", { class: "agent", text: e.agent }));
  const body = h("div", { style: "min-width:0; flex:1" });
  body.append(h("div", { class: "reasonrow", text: e.content }));
  li.append(body,
    h("span", { class: "dim3", text: e.tokens === null || e.tokens === undefined ? "" : fmt(e.tokens, 0) + " tok" }));
  return li;
}
/* The unified process view [flight-recorder charter]: ONE timeline tracing the
   stack's thought AND action to the solution. Interleaving uses only what the
   record DECLARES — a reasoning entry with a v3 ``turn`` renders before its
   step (thought precedes action); one with only ``relative_ts`` merges by the
   shared trial clock; one with neither is listed unlinked in capture order.
   Nothing is inferred, reordered by guesswork, or dropped. */
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
  card.append(h("div", { class: "dim3", style: "margin-bottom:8px",
    text: "one timeline — thought (flight recorder) interleaved with action (trajectory), both sha-verified; placement is the stack's own declared linkage, never inferred" }));
  const byTurn = {}, tsOnly = [], unlinked = [];
  for (const e of entries) {
    const hasTurn = e.turn !== null && e.turn !== undefined;
    if (hasTurn && steps !== null && e.turn < steps.length)
      (byTurn[e.turn] = byTurn[e.turn] || []).push(e);
    else if (hasTurn)
      /* a DECLARED link whose step is unavailable is a broken link to state,
         never a license to re-place the thought by some other signal */
      unlinked.push({ entry: e, note: "declared turn " + e.turn + " — step unavailable" });
    else if (e.relative_ts !== null && e.relative_ts !== undefined) tsOnly.push(e);
    else unlinked.push({ entry: e, note: null });
  }
  tsOnly.sort((a, b) => a.relative_ts - b.relative_ts);  /* stable: capture order breaks ties */
  const ul = h("ul", { class: "steps" });
  let cursor = 0;
  const flushTs = (limit) => {
    while (cursor < tsOnly.length && (limit === null || tsOnly[cursor].relative_ts <= limit)) {
      ul.append(thoughtLi(tsOnly[cursor])); cursor += 1;
    }
  };
  (steps || []).forEach((s, i) => {
    if (s.relative_ts !== null && s.relative_ts !== undefined) flushTs(s.relative_ts);
    for (const e of byTurn[i] || []) ul.append(thoughtLi(e));  /* thought precedes its action */
    ul.append(stepLi(s));
  });
  flushTs(null);
  card.append(ul);
  if (unlinked.length) {
    card.append(h("h2", { style: "margin-top:12px", text: "Unlinked reasoning (capture order)" }));
    card.append(h("div", { class: "dim3", style: "margin-bottom:4px",
      text: "spans whose position in the timeline is not declared — they belong to this trial, placement unknown (honest, not hidden)" }));
    const ul2 = h("ul", { class: "steps" });
    for (const u of unlinked) {
      const li = thoughtLi(u.entry);
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
     absence stated, never zeroed [EVAL-4-D004] */
  const tel = rec.telemetry || {};
  const telBits = [
    "cost " + (tel.cost === null || tel.cost === undefined ? "not measured" : fmt(tel.cost)),
    "wall " + (tel.wall_time_s === null || tel.wall_time_s === undefined ? "not measured" : fmtDur(tel.wall_time_s)),
    "tokens in " + (tel.tokens_in === null || tel.tokens_in === undefined ? "—" : fmt(tel.tokens_in, 0))
      + " · out " + (tel.tokens_out === null || tel.tokens_out === undefined ? "—" : fmt(tel.tokens_out, 0)),
    "tool calls " + (tel.tool_calls === null || tel.tool_calls === undefined ? "—" : fmt(tel.tool_calls, 0)),
  ];
  head.append(h("div", { class: "dim3", style: "flex-basis:100%", text: telBits.join("  ·  ") }));
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
      for (const s of steps) ul.append(stepLi(s));
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
  // and name the model each arm actually ran
  return h("div", { class: "armhead" },
    h("div", {}, "A · " + c.arm_a, h("span", { class: "amodel", text: shortModel(c.arm_a_model) })),
    h("div", {}, "B · " + c.arm_b, h("span", { class: "amodel", text: shortModel(c.arm_b_model) })));
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

/* a holdout state chip that keeps null honest: pass green, fail red,
   ungraded PLAIN — unmeasured must never wear failure red [EVAL-4-D004] */
function holdoutChip(armName, pass) {
  if (pass === null || pass === undefined)
    return h("span", { class: "chip", text: armName + " holdout: ungraded" });
  return h("span", { class: "chip " + (pass ? "ok" : "bad"),
    text: armName + " holdout " + (pass ? "✓" : "✕") });
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
  head.append(h("b", { text: "Compare — A: " + c.arm_a + " (" + shortModel(c.arm_a_model) + ")  vs  B: " + c.arm_b + " (" + shortModel(c.arm_b_model) + ")" }),
    h("span", { class: "chip" + (c.official_ready ? " ok" : ""), text: c.official_ready ? "official fence PASSES" : "EXPLORATORY — official fence not passed" }));
  head.append(h("span", { class: "spacer" }),
    h("span", { class: "chip click" + (only ? " on" : ""), tabindex: "0", text: "only disagreements (" + c.summary.disagreements + ")" + (only ? " ✕" : ""),
      onclick: () => setParam("only", only ? null : "disagreements") }));
  app.append(head);
  { // plain-language result banner: who won on the primary metric + advisory judge
    const H = c.summary.holdout, J = c.summary.judge;
    const pairs = H.a_only + H.b_only + H.both + H.neither;
    const ungraded = c.summary.pairs - pairs;
    const aPass = H.a_only + H.both, bPass = H.b_only + H.both;
    const holdoutOut = aPass > bPass ? "→ " + c.arm_a : (bPass > aPass ? "→ " + c.arm_b : "→ tie");
    const judgeLean = J.b > J.a ? "→ leans " + c.arm_b : (J.a > J.b ? "→ leans " + c.arm_a : "→ tie/mixed");
    const banner = h("div", { class: "card banner" });
    const l1 = h("div", {}, h("span", { class: "dim", text: "Primary (holdout pass): " }),
      c.arm_a + " " + aPass + "/" + pairs + " · " + c.arm_b + " " + bPass + "/" + pairs + "  ",
      h("b", { text: holdoutOut }));
    /* honest denominator: pairs the tallies exclude are named, not hidden */
    if (ungraded > 0) l1.append(h("span", { class: "dim3", text: "  · " + ungraded + " of " + c.summary.pairs + " pair(s) not fully graded, excluded from these tallies" }));
    banner.append(l1,
      h("div", { style: "margin-top:3px" }, h("span", { class: "dim", text: "Judge: " }),
        c.arm_a + " " + J.a + " · " + c.arm_b + " " + J.b + " · tie " + J.tie + "  ",
        h("b", { text: judgeLean }),
        h("span", { class: "dim3", text: "  — advisory, not an official verdict" })));
    app.append(banner);
  }
  /* tallies are navigation [EVAL-19 AC-5]: every count filters to its slice,
     and the slice lives in the URL. Predicates mirror compare.py's summary
     arithmetic exactly, so a tally always equals its filtered row count. */
  const slice = S.route.params.get("slice") || "";
  const knownSlice = ["holdout:a_only", "holdout:b_only", "holdout:both", "holdout:neither",
                      "judge:a", "judge:b", "judge:tie", "judge:cant", "judge:unjudged"];
  const tally = (key, label, count, why) => h("span", {
    class: "chip click" + (slice === key ? " on" : ""), tabindex: "0",
    title: why || "", text: label + " " + count + (slice === key ? " ✕" : ""),
    onclick: () => setParam("slice", slice === key ? null : key) });
  const sm = h("div", { class: "toolbar card" });
  sm.append(h("span", { class: "dim", text: "holdout: " }),
    tally("holdout:a_only", c.arm_a, c.summary.holdout.a_only, "pairs where only " + c.arm_a + " passed the holdout"),
    tally("holdout:b_only", c.arm_b, c.summary.holdout.b_only, "pairs where only " + c.arm_b + " passed the holdout"),
    tally("holdout:both", "both", c.summary.holdout.both, "pairs where both arms passed"),
    tally("holdout:neither", "neither", c.summary.holdout.neither, "pairs where both arms failed"),
    h("span", { class: "dim", text: " judge (ADVISORY): " }),
    tally("judge:a", "A · " + c.arm_a, c.summary.judge.a),
    tally("judge:b", "B · " + c.arm_b, c.summary.judge.b),
    tally("judge:tie", "tie", c.summary.judge.tie),
    tally("judge:cant", "cant", c.summary.judge.cant),
    tally("judge:unjudged", "unjudged", c.summary.judge.unjudged),
    h("span", { class: "spacer" }),
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
  /* pair index: the scannable overview of what the full cards below say;
     a row jumps to its card, so the wall of diffs is navigable */
  if (pairs.length > 1) {
    const idx = h("div", { class: "card" });
    idx.append(h("h2", { text: "Pairs (" + pairs.length + " shown) — click a row to jump to its diff" }));
    const table = h("table");
    table.append(h("tr", {}, ...["task · rep", c.arm_a + " (A)", c.arm_b + " (B)", "judge (ADVISORY)", ""].map(x => h("th", { text: x }))));
    const holdoutMark = (v) => v === null || v === undefined
      ? h("span", { class: "dim3", text: "ungraded" })
      : h("span", { class: v ? "chip ok" : "chip bad", text: v ? "✓ pass" : "✕ fail" });
    for (const p of pairs) {
      const tr = h("tr", { class: "row" });
      tr.addEventListener("click", () => {
        const el = document.getElementById("pair-" + p.comparison_id);
        if (el) el.scrollIntoView();
      });
      const jw = p.judge ? p.judge.winner : null;
      const jname = jw === "A" ? "A · " + c.arm_a : (jw === "B" ? "B · " + c.arm_b : jw);
      tr.append(h("td", {}, h("b", { text: p.task_id }), h("span", { class: "dim3", text: " · rep " + p.repetition })),
        h("td", {}, holdoutMark(p.a.holdout_pass)),
        h("td", {}, holdoutMark(p.b.holdout_pass)),
        h("td", { class: jw ? "" : "dim3", text: jw ? jname : "unjudged" }),
        h("td", {}, ...(p.disagreement ? [h("span", { class: "chip warn", text: "disagreement" })] : [])));
      table.append(tr);
    }
    idx.append(table);
    app.append(idx);
  }
  /* which flight recorders are open is view state and rides the URL
     (?fr=id,id — each id URI-encoded, comma-joined); a DOM-only <details>
     toggle would be wiped by the next poll re-render within POLL_MS */
  const frOpen = new Set((S.route.params.get("fr") || "").split(",").filter(Boolean));
  for (const p of pairs) {
    const card = h("div", { class: "card pairjump", id: "pair-" + p.comparison_id });
    const bar = h("div", { class: "toolbar" });
    bar.append(h("b", { text: p.task_id + " · rep " + p.repetition }),
      holdoutChip(c.arm_a, p.a.holdout_pass),
      holdoutChip(c.arm_b, p.b.holdout_pass));
    if (p.judge) { const w = p.judge.winner;
      bar.append(h("span", { class: "chip", text: "judge: " + (w === "A" ? "A · " + c.arm_a : (w === "B" ? "B · " + c.arm_b : w)) + " (ADVISORY)" })); }
    if (p.disagreement) bar.append(h("span", { class: "chip warn", title: "the deterministic grades and/or the advisory judge point different ways", text: "disagreement" }));
    card.append(bar);
    card.append(armHead(c));
    card.append(h("div", { class: "dim3 difflegend", text: "diff — red: only in A/" + c.arm_a + " · green: only in B/" + c.arm_b + " · unhighlighted: identical in both" }));
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
      card.append(fr);
    }
    app.append(card);
  }
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