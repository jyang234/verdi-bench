"""The authoring page [EVAL-17 AC-4, AC-5; D002].

One self-contained HTML document (the operator page's discipline: inline
CSS + script, relative fetches only, no external references, both themes).
The editable text panes are canonical (D002): the wizard's template button
generates YAML into the pane once, Save writes those exact bytes, and every
preview — validation, power, schedule, sha — is a GET over what Save wrote.
The sha shown beside the Lock button is computed from the same bytes the
lock will hash; the ceremony displays it before asking for attestation.

A locked draft renders read-only: panes disabled, no Save, no Lock — the
immutability of pre-registration as a visible fact [AC-3].
"""

from __future__ import annotations

import json
from pathlib import Path

# The template pane is seeded from the ONE canonical starter spec [refactor 02
# §2] so it can never drift from the docs example / test builders again. The
# sdk-is-a-leaf import contract forbids importing the sdk package, so the shared
# template DATA file is read directly — the file is the contract, not the code.
_STARTER_SPEC_JSON = json.dumps(
    (
        Path(__file__).resolve().parent.parent
        / "sdk" / "templates" / "starter-experiment.yaml"
    ).read_text(encoding="utf-8")
)

AUTHOR_PAGE = ("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>verdi-bench — author</title>
<style>
  :root {
    --surface-1: #fcfcfb; --plane: #f9f9f7;
    --ink-1: #0b0b0b; --ink-2: #52514e; --ink-3: #898781;
    --hairline: #e1e0d9; --border: rgba(11,11,11,0.10);
    --accent: #2a78d6; --soft: #eef3fa;
    --good: #0ca30c; --warning: #9a6b00; --critical: #d03b3b;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --surface-1: #1a1a19; --plane: #0d0d0d;
      --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-3: #898781;
      --hairline: #2c2c2a; --border: rgba(255,255,255,0.10);
      --accent: #3987e5; --soft: #1c2733;
      --good: #0ca30c; --warning: #d9a21b; --critical: #e05252;
    }
  }
  * { box-sizing: border-box; margin: 0; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         background: var(--plane); color: var(--ink-1); padding: 18px; font-size: 14px; line-height: 1.45; }
  main { max-width: 1180px; margin: 0 auto; display: grid; gap: 12px; }
  .banner { background: var(--surface-1); border: 1px solid var(--border);
            border-left: 3px solid var(--accent); border-radius: 6px; padding: 9px 14px;
            color: var(--ink-2); font-size: 13px; }
  .banner strong { color: var(--ink-1); }
  header.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  header.bar .crumb { font-size: 16px; font-weight: 650; }
  header.bar .crumb .up { color: var(--ink-3); font-weight: 400; cursor: pointer; }
  header.bar .crumb .up:hover { color: var(--ink-1); text-decoration: underline; }
  .chip { display: inline-flex; align-items: center; gap: 5px; padding: 1px 9px; border-radius: 999px;
          border: 1px solid var(--border); background: var(--surface-1); color: var(--ink-2);
          font-size: 12px; white-space: nowrap; }
  .chip.ok { color: var(--good); } .chip.bad { color: var(--critical); } .chip.warn { color: var(--warning); }
  .spacer { flex: 1; }
  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
  h2 { font-size: 13px; font-weight: 650; color: var(--ink-2); margin-bottom: 8px; }
  .btn { font: inherit; font-size: 12.5px; color: var(--ink-2); border: 1px solid var(--border);
         border-radius: 6px; padding: 4px 12px; background: var(--surface-1); cursor: pointer; }
  .btn:hover { border-color: var(--ink-3); color: var(--ink-1); }
  .btn.primary { color: var(--surface-1); background: var(--accent); border-color: var(--accent); font-weight: 600; }
  .btn:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  input.field { font: inherit; font-size: 13px; color: var(--ink-1); background: var(--surface-1);
                border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; min-width: 200px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th { text-align: left; color: var(--ink-3); font-size: 11.5px; font-weight: 500;
       border-bottom: 1px solid var(--hairline); padding: 4px 8px 6px 0; }
  td { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--hairline); }
  tr:last-child td { border-bottom: none; }
  tr.row { cursor: pointer; } tr.row:hover td { background: var(--soft); }
  .mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; }
  .dim { color: var(--ink-2); } .dim3 { color: var(--ink-3); }
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .split { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(300px, 1fr); gap: 12px; align-items: start; }
  textarea.pane { width: 100%; min-height: 380px; resize: vertical;
                  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12.5px;
                  line-height: 1.5; color: var(--ink-1); background: var(--surface-1);
                  border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; }
  textarea.pane:disabled { color: var(--ink-2); background: var(--plane); }
  .tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
  .tab { font-size: 12px; padding: 2px 11px; border-radius: 999px; border: 1px solid var(--border);
         cursor: pointer; color: var(--ink-2); background: var(--surface-1); }
  .tab.on { background: var(--soft); border-color: var(--accent); color: var(--ink-1); }
  ul.plain { list-style: none; display: grid; gap: 4px; }
  ul.plain li { font-size: 12.5px; color: var(--ink-2); display: flex; gap: 8px; align-items: baseline; }
  .err { color: var(--critical); }
  pre.msg { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; color: var(--ink-2);
            white-space: pre-wrap; word-break: break-word; background: var(--plane);
            border: 1px solid var(--hairline); border-radius: 6px; padding: 8px 10px; }
  label.ack { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; color: var(--ink-2); }
  .kbdrow { color: var(--ink-3); font-size: 12px; }
  @media (max-width: 880px) { .split { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<main>
  <div class="banner"><strong>Authoring surface — pre-registration only.</strong>
    Drafts are plain directories; nothing here is an experiment until you lock it,
    and the lock is the surface's only ledgered operation. After the lock, the
    pre-registration is immutable: re-planning means a new draft. Stage execution
    stays in the CLI; the operator view watches it.</div>
  <header class="bar" id="bar"></header>
  <div id="app"></div>
  <div class="kbdrow">the text pane is canonical: what Save writes is what previews read and what the lock hashes</div>
</main>

<script>
"use strict";
const S = { drafts: null, actor: "", name: null, doc: null, tab: "experiment.yaml",
            dirty: {}, validate: null, power: null, schedule: null, lockError: null, lockResult: null };
window.__vb = () => ({ route: location.hash, name: S.name, locked: !!(S.doc && S.doc.locked),
                       dirty: Object.keys(S.dirty).length, sha: S.validate && S.validate.spec_sha256 });

const TPL_SPEC = __STARTER_SPEC_JSON__;  // the single canonical starter [refactor 02 §2]
const TPL_TASKS = "tasks:\\n  - id: task-1\\n    prompt: describe the work\\n";
const TPL_RUBRIC = "Judge on correctness of the change against the task intent.\\n";
const PANES = ["experiment.yaml", "tasks.yaml", "rubrics/code-task-v1.md"];

async function j(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json();
  if (!r.ok) { const e = new Error(body.error || r.status); e.cls = body.error_class; throw e; }
  return body;
}
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "disabled") el.disabled = !!v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids) if (kid !== null && kid !== undefined) el.append(kid);
  return el;
}
const fmt = (n) => (n === null || n === undefined) ? "\\u2014" : String(n);

/* ------------------------------------------------------------------ data */
async function loadList() { const r = await j("/api/drafts"); S.drafts = r.drafts; S.actor = r.actor; }
async function openDraft(name) {
  S.name = name; S.doc = await j("/api/draft?name=" + encodeURIComponent(name));
  S.dirty = {}; S.lockError = null; S.lockResult = null;
  await refreshPreviews();
}
async function refreshPreviews() {
  S.validate = S.power = S.schedule = null;
  try { S.validate = await j("/api/validate?name=" + encodeURIComponent(S.name)); } catch (e) { S.validate = { unavailable: String(e.message) }; }
  if (S.validate && S.validate.spec && S.validate.spec.ok) {
    try { S.power = await j("/api/power?name=" + encodeURIComponent(S.name) + "&quick=1"); } catch (e) { S.power = { unavailable: String(e.message) }; }
    try { S.schedule = await j("/api/schedule?name=" + encodeURIComponent(S.name) + "&limit=12"); } catch (e) { S.schedule = { unavailable: String(e.message) }; }
  }
  render();
}
async function saveDraft() {
  const files = {};
  for (const [rel, text] of Object.entries(S.dirty)) files[rel] = text;
  if (!Object.keys(files).length) return;
  const r = await j("/api/draft", { method: "POST", headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ name: S.name, files }) });
  for (const rel of r.saved) { S.doc.files[rel] = files[rel]; delete S.dirty[rel]; }
  await refreshPreviews();
}
async function doLock(attestedBy, ack) {
  S.lockError = null;
  try {
    S.lockResult = await j("/api/lock", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: S.name, attested_by: attestedBy, acknowledge_underpowered: ack }) });
    S.doc = await j("/api/draft?name=" + encodeURIComponent(S.name));
  } catch (e) { S.lockError = { cls: e.cls || "error", message: e.message }; }
  render();
}

/* ------------------------------------------------------------------ render */
function renderBar() {
  const bar = document.getElementById("bar");
  bar.textContent = "";
  const crumb = h("div", { class: "crumb" });
  if (!S.name) crumb.append("drafts");
  else {
    crumb.append(h("span", { class: "up", text: "drafts / ", onclick: () => { location.hash = "#/"; } }),
                 h("span", { text: S.name }));
  }
  bar.append(crumb);
  if (S.doc) bar.append(h("span", { class: "chip" + (S.doc.locked ? " ok" : ""),
                                    text: S.doc.locked ? "\\u2713 locked (immutable)" : "draft" }));
  bar.append(h("span", { class: "spacer" }), h("span", { class: "chip", text: "actor: " + (S.actor || "?") }));
}

function renderList(app) {
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Drafts & experiments" }));
  const bar = h("div", { class: "toolbar", style: "margin-bottom:10px" });
  const nameInput = h("input", { class: "field", placeholder: "new-draft-name" });
  bar.append(nameInput, h("button", { class: "btn primary", text: "New draft", onclick: async () => {
    const name = nameInput.value.trim();
    if (!name) return;
    await j("/api/draft", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, files: { "experiment.yaml": TPL_SPEC, "tasks.yaml": TPL_TASKS,
                                            "rubrics/code-task-v1.md": TPL_RUBRIC } }) });
    location.hash = "#/draft/" + encodeURIComponent(name);
  } }));
  card.append(bar);
  const rows = S.drafts || [];
  if (!rows.length) card.append(h("div", { class: "dim3", text: "No drafts yet — name one above; the template lands in an editable pane." }));
  else {
    const table = h("table");
    table.append(h("tr", {}, h("th", { text: "name" }), h("th", { text: "state" }), h("th", { text: "files" })));
    for (const d of rows) {
      const tr = h("tr", { class: "row", onclick: () => { location.hash = "#/draft/" + encodeURIComponent(d.name); } });
      tr.append(h("td", {}, h("b", { text: d.name })),
                h("td", {}, h("span", { class: "chip" + (d.locked ? " ok" : ""), text: d.locked ? "locked" : "draft" })),
                h("td", { class: "dim3", text: [d.has_spec ? "spec" : null, d.has_tasks ? "tasks" : null].filter(Boolean).join(" \\u00b7 ") }));
      table.append(tr);
    }
    card.append(table);
  }
  app.append(card);
}

function paneText(rel) {
  if (rel in S.dirty) return S.dirty[rel];
  return (S.doc.files && S.doc.files[rel]) || "";
}

function renderEditor(app) {
  const locked = S.doc.locked;
  const wrap = h("div", { class: "split" });

  const editor = h("div", { class: "card" });
  const tabs = h("div", { class: "tabs" });
  const known = new Set(PANES.concat(Object.keys(S.doc.files || {})));
  for (const rel of [...known]) {
    tabs.append(h("span", { class: "tab" + (S.tab === rel ? " on" : ""), text: rel + (rel in S.dirty ? " \\u25cf" : ""),
                            onclick: () => { S.tab = rel; render(); } }));
  }
  editor.append(tabs);
  const pane = h("textarea", { class: "pane", disabled: locked, value: paneText(S.tab) });
  pane.addEventListener("input", () => { S.dirty[S.tab] = pane.value; });
  pane.addEventListener("blur", () => render());
  editor.append(pane);
  const bar = h("div", { class: "toolbar", style: "margin-top:8px" });
  if (!locked) {
    bar.append(h("button", { class: "btn primary", text: "Save draft", onclick: () => saveDraft() }));
    bar.append(h("button", { class: "btn", text: "Insert template into this pane", onclick: () => {
      const tpl = S.tab === "experiment.yaml" ? TPL_SPEC : S.tab === "tasks.yaml" ? TPL_TASKS : TPL_RUBRIC;
      S.dirty[S.tab] = tpl; render();
    } }));
    bar.append(h("span", { class: "dim3", text: Object.keys(S.dirty).length ? "unsaved changes — previews read the last save" : "saved" }));
  } else {
    bar.append(h("span", { class: "dim3", text: "locked: the pre-registration is immutable; re-plan in a new draft" }));
  }
  editor.append(bar);
  wrap.append(editor);

  const side = h("div", { style: "display:grid; gap:12px" });
  side.append(renderValidation(), renderPower(), renderSchedule(), renderCeremony());
  wrap.append(side);
  app.append(wrap);
}

function renderValidation() {
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Validation (of the saved draft)" }));
  const v = S.validate;
  if (!v) { card.append(h("div", { class: "dim3", text: "\\u2026" })); return card; }
  if (v.unavailable) { card.append(h("div", { class: "err", text: v.unavailable })); return card; }
  const ul = h("ul", { class: "plain" });
  ul.append(h("li", {}, h("span", { class: "chip", text: "sha" }),
                      h("span", { class: "mono", text: (v.spec_sha256 || "").slice(0, 16) + "\\u2026" })));
  if (v.spec.ok) {
    ul.append(h("li", {}, h("span", { class: "chip ok", text: "\\u2713 spec" }),
      h("span", { class: "dim", text: v.spec.arms.join(" vs ") + " \\u00b7 reps " + v.spec.repetitions +
        " \\u00b7 seed " + v.spec.seed + " \\u00b7 " + v.spec.primary_metric })));
    ul.append(h("li", {}, h("span", { class: "chip" + (v.spec.rubric_present ? " ok" : " bad"),
      text: v.spec.rubric_present ? "\\u2713 rubric" : "\\u2715 rubric missing" }),
      h("span", { class: "dim3", text: v.spec.rubric })));
    if (v.platform) ul.append(h("li", {}, h("span", { class: "chip" + (v.platform.ok ? " ok" : " bad"),
      text: v.platform.ok ? "\\u2713 platforms" : "\\u2715 " + v.platform.error_class }),
      h("span", { class: "dim3", text: v.platform.ok ? v.spec.arms.join(", ") : "unrunnable arm platform \\u2014 the lock will refuse" })));
  } else {
    ul.append(h("li", {}, h("span", { class: "chip bad", text: "\\u2715 " + v.spec.error_class })));
    card.append(ul, h("pre", { class: "msg", text: v.spec.error }));
    return card;
  }
  if (v.tasks.ok) ul.append(h("li", {}, h("span", { class: "chip ok", text: "\\u2713 tasks" }),
    h("span", { class: "dim", text: v.tasks.count + " task(s): " + v.tasks.ids.join(", ") })));
  else { ul.append(h("li", {}, h("span", { class: "chip bad", text: "\\u2715 tasks" }))); card.append(ul, h("pre", { class: "msg", text: v.tasks.error })); return card; }
  card.append(ul);
  return card;
}

function renderPower() {
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Power preview" }));
  const p = S.power;
  if (!p) { card.append(h("div", { class: "dim3", text: "valid spec required" })); return card; }
  if (p.unavailable) { card.append(h("div", { class: "err", text: p.unavailable })); return card; }
  const m = p.mde;
  card.append(h("div", {}, h("b", { text: "MDE " + fmt(m.mde) }),
    h("span", { class: "dim3", text: "  flags: " + ((m.flags || []).join(", ") || "none") })));
  const curve = (m.power_curve || []).map(pt => pt.delta + "\\u2192" + Math.round(pt.power * 100) + "%").join("  ");
  card.append(h("div", { class: "mono dim", style: "margin-top:4px", text: curve }));
  if (p.quick) card.append(h("div", { class: "dim3", style: "margin-top:4px", text: p.note }));
  return card;
}

function renderSchedule() {
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Derived interleave (deterministic in the seed)" }));
  const s = S.schedule;
  if (!s) { card.append(h("div", { class: "dim3", text: "valid spec + tasks required" })); return card; }
  if (s.unavailable) { card.append(h("div", { class: "err", text: s.unavailable })); return card; }
  card.append(h("div", { class: "mono dim",
    text: s.order.map(t => t.task_id + "/" + t.arm + "#" + t.repetition).join(" \\u2192 ") +
          (s.total > s.order.length ? " \\u2026 (" + s.total + " total)" : "") }));
  return card;
}

function renderCeremony() {
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Lock ceremony" }));
  if (S.doc.locked) {
    const l = S.doc.lock || {};
    card.append(h("ul", { class: "plain" },
      h("li", {}, h("span", { class: "chip ok", text: "\\u2713 locked" }),
        h("span", { class: "mono", text: (l.spec_sha256 || "").slice(0, 16) + "\\u2026" })),
      h("li", {}, h("span", { class: "dim", text: "seed " + fmt(l.seed) + " \\u00b7 attested by " + fmt(l.attested_by) + " \\u00b7 " + fmt(l.ts) }))));
    return card;
  }
  const ok = S.validate && S.validate.spec && S.validate.spec.ok && S.validate.tasks && S.validate.tasks.ok
             && S.validate.spec.rubric_present && (!S.validate.platform || S.validate.platform.ok)
             && !Object.keys(S.dirty).length;
  card.append(h("div", { class: "dim", style: "margin-bottom:8px",
    text: "Locks the saved bytes (sha above) with a ledgered attestation; the lock recomputes power at full fidelity. One lock per experiment — there is no amend." }));
  const attested = h("input", { class: "field", placeholder: "attested_by (who vouches)" });
  const ackWrap = h("label", { class: "ack" });
  const ack = h("input", { type: "checkbox" });
  ackWrap.append(ack, h("span", { text: "acknowledge underpowered design (ledgered on the lock event)" }));
  const button = h("button", { class: "btn primary", text: "Lock pre-registration", disabled: !ok,
    onclick: () => doLock(attested.value.trim(), ack.checked) });
  card.append(h("div", { class: "toolbar" }, attested, button));
  card.append(ackWrap);
  if (!ok) card.append(h("div", { class: "dim3", style: "margin-top:6px",
    text: "requires: valid spec + tasks, registered arm platforms, rubric present, no unsaved changes" }));
  if (S.lockError) card.append(
    h("div", { style: "margin-top:8px" }, h("span", { class: "chip bad", text: "\\u2715 " + S.lockError.cls })),
    h("pre", { class: "msg", text: S.lockError.message }));
  if (S.lockResult) card.append(h("div", { style: "margin-top:8px" },
    h("span", { class: "chip ok", text: "\\u2713 locked " + S.lockResult.spec_sha256.slice(0, 16) + "\\u2026" })));
  return card;
}

function render() {
  renderBar();
  const app = document.getElementById("app");
  app.textContent = "";
  if (!S.name) renderList(app); else if (S.doc) renderEditor(app);
  else app.append(h("div", { class: "dim3", text: "loading\\u2026" }));
}

/* ------------------------------------------------------------------ boot */
async function route() {
  const m = (location.hash || "#/").match(/^#\\/draft\\/(.+)$/);
  await loadList();
  if (m) { await openDraft(decodeURIComponent(m[1])); } else { S.name = null; S.doc = null; }
  render();
}
window.addEventListener("hashchange", route);
route();
</script>
</body>
</html>
""").replace("__STARTER_SPEC_JSON__", _STARTER_SPEC_JSON)
