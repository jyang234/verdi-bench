"""The reviewer queue page [EVAL-18 AC-4, D003].

One self-contained document (the house needle property, inline script
allowed) whose whole vocabulary is blinded: comparisons, Response 1/2, and
the reviewer's own answers. It carries the inverse of the operator banner —
the standing instruction that a reviewer must not open the operator view for
experiments they review — and never renders an arm name before the ledgered
reveal for that comparison returns one.

Keyboard-first capture (D003, the parity-research queue ergonomics mapped to
our verdict vocabulary): 1 / 2 / T / C pick the winner, the two integrity
answers are required before submit enables, Enter records and advances,
j/k move the queue. ``window.__vb()`` is the headless-test seam.
"""

from __future__ import annotations

REVIEWER_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>verdi-bench — blinded review</title>
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
         background: var(--plane); color: var(--ink-1); padding: 18px; font-size: 14px; line-height: 1.5; }
  main { max-width: 980px; margin: 0 auto; display: grid; gap: 12px; }
  .banner { background: var(--surface-1); border: 1px solid var(--border);
            border-left: 3px solid var(--warning); border-radius: 6px; padding: 9px 14px;
            color: var(--ink-2); font-size: 13px; }
  .banner strong { color: var(--ink-1); }
  header.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  header.bar h1 { font-size: 16px; font-weight: 650; }
  .chip { display: inline-flex; align-items: center; gap: 5px; padding: 1px 9px; border-radius: 999px;
          border: 1px solid var(--border); background: var(--surface-1); color: var(--ink-2);
          font-size: 12px; white-space: nowrap; }
  .chip.ok { color: var(--good); }
  .spacer { flex: 1; }
  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
  h2 { font-size: 13px; font-weight: 650; color: var(--ink-2); margin-bottom: 8px; }
  .btn { font: inherit; font-size: 12.5px; color: var(--ink-2); border: 1px solid var(--border);
         border-radius: 6px; padding: 4px 12px; background: var(--surface-1); cursor: pointer; }
  .btn:hover { border-color: var(--ink-3); color: var(--ink-1); }
  .btn.primary { color: var(--surface-1); background: var(--accent); border-color: var(--accent); font-weight: 600; }
  .btn.pick { min-width: 108px; }
  .btn.on { background: var(--soft); border-color: var(--accent); color: var(--ink-1); }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  input.field { font: inherit; font-size: 13px; color: var(--ink-1); background: var(--surface-1);
                border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--ink-3); font-size: 11.5px; font-weight: 500;
       border-bottom: 1px solid var(--hairline); padding: 4px 8px 6px 0; }
  td { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--hairline); }
  tr:last-child td { border-bottom: none; }
  tr.row { cursor: pointer; } tr.row:hover td, tr.sel td { background: var(--soft); }
  .mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; }
  .dim { color: var(--ink-2); } .dim3 { color: var(--ink-3); }
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  label.q { display: flex; gap: 8px; align-items: baseline; font-size: 13px; color: var(--ink-2); }
  pre.msg { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; color: var(--ink-2);
            white-space: pre-wrap; word-break: break-word; background: var(--plane);
            border: 1px solid var(--hairline); border-radius: 6px; padding: 8px 10px; }
  kbd { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; border: 1px solid var(--hairline);
        border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; background: var(--surface-1); color: var(--ink-2); }
  .kbdrow { color: var(--ink-3); font-size: 12px; }
</style>
</head>
<body>
<main>
  <div class="banner">&#9888;&#65039; <strong>Blinded reviewer surface.</strong>
    You see Response 1 and Response 2 — never arms, models, or the judge's
    verdict — until you record yours and explicitly reveal. <strong>Do not open
    the operator view</strong> for experiments you review: it is unblinded and
    watching it disqualifies you. Your verdict and the reveal are each one
    ledgered event under your name.</div>
  <header class="bar" id="bar"></header>
  <div id="app"></div>
  <div class="kbdrow"><kbd>j</kbd>/<kbd>k</kbd> queue &#183; <kbd>1</kbd>/<kbd>2</kbd>/<kbd>t</kbd>/<kbd>c</kbd> winner &#183;
    <kbd>enter</kbd> record &amp; advance &#183; the packet opens in its own tab</div>
</main>

<script>
"use strict";
const S = { queue: null, sel: null, winner: null, reason: "", recognized: null,
            guess: "", busy: false, error: null, revealed: {} };
window.__vb = () => ({ pending: S.queue ? S.queue.pending.length : null,
                       done: S.queue ? S.queue.done.length : null,
                       sel: S.sel, winner: S.winner, recognized: S.recognized,
                       canSubmit: canSubmit() });

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
function canSubmit() {
  return !!(S.sel && S.winner && S.recognized !== null &&
            (S.recognized === false || S.guess.trim()));
}

async function load() {
  S.queue = await j("/api/queue");
  if (S.sel && !S.queue.pending.some(p => p.comparison_id === S.sel)) S.sel = null;
  if (!S.sel && S.queue.pending.length) S.sel = S.queue.pending[0].comparison_id;
  render();
}
function resetForm() { S.winner = null; S.reason = ""; S.recognized = null; S.guess = ""; S.error = null; }

async function submit() {
  if (!canSubmit() || S.busy) return;
  S.busy = true; S.error = null;
  try {
    await j("/api/verdict", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comparison_id: S.sel, winner: S.winner, reason: S.reason,
                             arm_recognized: S.recognized, arm_guess: S.recognized ? S.guess.trim() : null }) });
    resetForm(); S.sel = null;
    await load();  // advances to the next pending comparison
  } catch (e) { S.error = { cls: e.cls || "error", message: e.message }; render(); }
  S.busy = false;
}
async function reveal(cid) {
  try {
    const r = await j("/api/reveal", { method: "POST", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify({ comparison_id: cid }) });
    S.revealed[cid] = r.revealed;
  } catch (e) { S.revealed[cid] = { error: e.message }; }
  await load();
}

function renderBar() {
  const bar = document.getElementById("bar");
  bar.textContent = "";
  bar.append(h("h1", { text: "Blinded review queue" }));
  if (S.queue) {
    bar.append(h("span", { class: "chip", text: "reviewer: " + S.queue.reviewer }));
    bar.append(h("span", { class: "chip" + (S.queue.pending.length ? "" : " ok"),
      text: S.queue.done.length + " of " + S.queue.total + " recorded" }));
  }
  bar.append(h("span", { class: "spacer" }),
    h("button", { class: "btn", text: "Open packet \\u2197",
      onclick: () => window.open("/packet", "_blank") }));
}

function render() {
  renderBar();
  const app = document.getElementById("app");
  app.textContent = "";
  if (!S.queue) { app.append(h("div", { class: "card dim3", text: "loading\\u2026" })); return; }
  if (!S.queue.packet_built) {
    app.append(h("div", { class: "card dim",
      text: "No packet built yet — run `bench review build` first; this surface only ever serves built packet bytes." }));
    return;
  }

  const cap = h("div", { class: "card" });
  cap.append(h("h2", { text: "Capture (strictly before reveal)" }));
  if (!S.sel) cap.append(h("div", { class: "dim3", text: S.queue.pending.length ? "select a pending comparison" : "queue complete \\u2014 every packet comparison has a verdict" }));
  else {
    cap.append(h("div", { style: "margin-bottom:8px" }, h("b", { class: "mono", text: S.sel }),
      h("span", { class: "dim3", text: "  read it in the packet tab, then answer here" })));
    const picks = h("div", { class: "toolbar", style: "margin-bottom:8px" });
    for (const [key, label] of [["1", "Response 1 (1)"], ["2", "Response 2 (2)"],
                                 ["TIE", "Tie (t)"], ["CANT_JUDGE", "Can't judge (c)"]])
      picks.append(h("button", { class: "btn pick" + (S.winner === key ? " on" : ""), text: label,
        onclick: () => { S.winner = key; render(); } }));
    cap.append(picks);
    const reason = h("input", { class: "field", style: "width:100%", placeholder: "reason (optional)", value: S.reason });
    reason.addEventListener("input", () => { S.reason = reason.value; });
    cap.append(reason);
    const integ = h("div", { style: "margin-top:10px; display:grid; gap:6px" });
    integ.append(h("div", { class: "dim", text: "Blinding integrity (required, ledgered with your verdict):" }));
    const yes = h("input", { type: "radio", name: "rec", checked: S.recognized === true,
                             onchange: () => { S.recognized = true; render(); } });
    const no = h("input", { type: "radio", name: "rec", checked: S.recognized === false,
                            onchange: () => { S.recognized = false; render(); } });
    integ.append(h("label", { class: "q" }, yes, h("span", { text: "I believe I can identify which arm produced a response" })));
    integ.append(h("label", { class: "q" }, no, h("span", { text: "I cannot identify the arms" })));
    if (S.recognized === true) {
      const guess = h("input", { class: "field", placeholder: "your guess for Response 1's arm", value: S.guess });
      guess.addEventListener("input", () => { S.guess = guess.value; });
      integ.append(guess);
    }
    cap.append(integ);
    cap.append(h("div", { class: "toolbar", style: "margin-top:10px" },
      h("button", { class: "btn primary", text: "Record verdict (enter)", disabled: !canSubmit() || S.busy,
                    onclick: () => submit() }),
      h("span", { class: "dim3", text: "one ledgered human_verdict; the reveal is a separate act" })));
    if (S.error) cap.append(h("pre", { class: "msg", style: "margin-top:8px",
      text: S.error.cls + ": " + S.error.message }));
  }
  app.append(cap);

  const list = h("div", { class: "card" });
  list.append(h("h2", { text: "Queue" }));
  const table = h("table");
  table.append(h("tr", {}, h("th", { text: "comparison" }), h("th", { text: "task" }),
                          h("th", { text: "state" }), h("th", {})));
  for (const item of S.queue.pending) {
    const tr = h("tr", { class: "row" + (item.comparison_id === S.sel ? " sel" : ""),
                         onclick: () => { S.sel = item.comparison_id; resetForm(); render(); } });
    tr.append(h("td", { class: "mono", text: item.comparison_id }),
              h("td", { text: item.task_id || "" }),
              h("td", {}, h("span", { class: "chip", text: "pending" })), h("td"));
    table.append(tr);
  }
  for (const item of S.queue.done) {
    const tr = h("tr");
    tr.append(h("td", { class: "mono", text: item.comparison_id }),
              h("td", { text: item.task_id || "" }),
              h("td", {}, h("span", { class: "chip ok", text: item.revealed ? "revealed" : "recorded" })));
    const td = h("td");
    if (!item.revealed)
      td.append(h("button", { class: "btn", text: "Reveal (ledgered)",
                              onclick: () => reveal(item.comparison_id) }));
    const shown = S.revealed[item.comparison_id];
    if (shown) td.append(h("pre", { class: "msg", text: JSON.stringify(shown, null, 1) }));
    tr.append(td);
    table.append(tr);
  }
  if (!S.queue.pending.length && !S.queue.done.length)
    list.append(h("div", { class: "dim3", text: "the packet selected no comparisons" }));
  else list.append(table);
  app.append(list);
}

document.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) {
    if (e.key === "Enter" && e.target.type !== "radio") { submit(); }
    return;
  }
  const keys = { "1": "1", "2": "2", "t": "TIE", "c": "CANT_JUDGE" };
  if (keys[e.key] && S.sel) { S.winner = keys[e.key]; render(); }
  else if (e.key === "Enter") submit();
  else if ((e.key === "j" || e.key === "k") && S.queue && S.queue.pending.length) {
    const ids = S.queue.pending.map(p => p.comparison_id);
    let i = Math.max(0, ids.indexOf(S.sel));
    i = Math.max(0, Math.min(ids.length - 1, i + (e.key === "j" ? 1 : -1)));
    if (ids[i] !== S.sel) { S.sel = ids[i]; resetForm(); render(); }
  }
});

load();
setInterval(load, 4000);
</script>
</body>
</html>
"""
