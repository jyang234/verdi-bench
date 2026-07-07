"use strict";
const S = { drafts: null, actor: "", name: null, doc: null, tab: "experiment.yaml",
            dirty: {}, validate: null, power: null, schedule: null, lockError: null, lockResult: null };
window.__vb = () => ({ route: location.hash, name: S.name, locked: !!(S.doc && S.doc.locked),
                       dirty: Object.keys(S.dirty).length, sha: S.validate && S.validate.spec_sha256 });

const TPL_SPEC = __STARTER_SPEC_JSON__;  // the single canonical starter [refactor 02 §2]
const TPL_TASKS = "tasks:\n  - id: task-1\n    prompt: describe the work\n";
const TPL_RUBRIC = "Judge on correctness of the change against the task intent.\n";
const PANES = ["experiment.yaml", "tasks.yaml", "rubrics/code-task-v1.md"];

/*@@KIT@@*/
const fmt = (n) => (n === null || n === undefined) ? "—" : String(n);

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
                                    text: S.doc.locked ? "✓ locked (immutable)" : "draft" }));
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
                h("td", { class: "dim3", text: [d.has_spec ? "spec" : null, d.has_tasks ? "tasks" : null].filter(Boolean).join(" · ") }));
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
    tabs.append(h("span", { class: "tab" + (S.tab === rel ? " on" : ""), text: rel + (rel in S.dirty ? " ●" : ""),
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
  if (!v) { card.append(h("div", { class: "dim3", text: "…" })); return card; }
  if (v.unavailable) { card.append(h("div", { class: "err", text: v.unavailable })); return card; }
  const ul = h("ul", { class: "plain" });
  ul.append(h("li", {}, h("span", { class: "chip", text: "sha" }),
                      h("span", { class: "mono", text: (v.spec_sha256 || "").slice(0, 16) + "…" })));
  if (v.spec.ok) {
    ul.append(h("li", {}, h("span", { class: "chip ok", text: "✓ spec" }),
      h("span", { class: "dim", text: v.spec.arms.join(" vs ") + " · reps " + v.spec.repetitions +
        " · seed " + v.spec.seed + " · " + v.spec.primary_metric })));
    ul.append(h("li", {}, h("span", { class: "chip" + (v.spec.rubric_present ? " ok" : " bad"),
      text: v.spec.rubric_present ? "✓ rubric" : "✕ rubric missing" }),
      h("span", { class: "dim3", text: v.spec.rubric })));
    if (v.platform) ul.append(h("li", {}, h("span", { class: "chip" + (v.platform.ok ? " ok" : " bad"),
      text: v.platform.ok ? "✓ platforms" : "✕ " + v.platform.error_class }),
      h("span", { class: "dim3", text: v.platform.ok ? v.spec.arms.join(", ") : "unrunnable arm platform — the lock will refuse" })));
  } else {
    ul.append(h("li", {}, h("span", { class: "chip bad", text: "✕ " + v.spec.error_class })));
    card.append(ul, h("pre", { class: "msg", text: v.spec.error }));
    return card;
  }
  if (v.tasks.ok) ul.append(h("li", {}, h("span", { class: "chip ok", text: "✓ tasks" }),
    h("span", { class: "dim", text: v.tasks.count + " task(s): " + v.tasks.ids.join(", ") })));
  else { ul.append(h("li", {}, h("span", { class: "chip bad", text: "✕ tasks" }))); card.append(ul, h("pre", { class: "msg", text: v.tasks.error })); return card; }
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
  const curve = (m.power_curve || []).map(pt => pt.delta + "→" + Math.round(pt.power * 100) + "%").join("  ");
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
    text: s.order.map(t => t.task_id + "/" + t.arm + "#" + t.repetition).join(" → ") +
          (s.total > s.order.length ? " … (" + s.total + " total)" : "") }));
  return card;
}

function renderCeremony() {
  const card = h("div", { class: "card" });
  card.append(h("h2", { text: "Lock ceremony" }));
  if (S.doc.locked) {
    const l = S.doc.lock || {};
    card.append(h("ul", { class: "plain" },
      h("li", {}, h("span", { class: "chip ok", text: "✓ locked" }),
        h("span", { class: "mono", text: (l.spec_sha256 || "").slice(0, 16) + "…" })),
      h("li", {}, h("span", { class: "dim", text: "seed " + fmt(l.seed) + " · attested by " + fmt(l.attested_by) + " · " + fmt(l.ts) }))));
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
    h("div", { style: "margin-top:8px" }, h("span", { class: "chip bad", text: "✕ " + S.lockError.cls })),
    h("pre", { class: "msg", text: S.lockError.message }));
  if (S.lockResult) card.append(h("div", { style: "margin-top:8px" },
    h("span", { class: "chip ok", text: "✓ locked " + S.lockResult.spec_sha256.slice(0, 16) + "…" })));
  return card;
}

function render() {
  renderBar();
  const app = document.getElementById("app");
  app.textContent = "";
  if (!S.name) renderList(app); else if (S.doc) renderEditor(app);
  else app.append(h("div", { class: "dim3", text: "loading…" }));
}

/* ------------------------------------------------------------------ boot */
async function route() {
  const m = (location.hash || "#/").match(/^#\/draft\/(.+)$/);
  await loadList();
  if (m) { await openDraft(decodeURIComponent(m[1])); } else { S.name = null; S.doc = null; }
  render();
}
window.addEventListener("hashchange", route);
route();