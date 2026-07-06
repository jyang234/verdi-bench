"use strict";
const S = { queue: null, sel: null, winner: null, reason: "", recognized: null,
            guess: "", busy: false, error: null, revealed: {} };
window.__vb = () => ({ pending: S.queue ? S.queue.pending.length : null,
                       done: S.queue ? S.queue.done.length : null,
                       sel: S.sel, winner: S.winner, recognized: S.recognized,
                       canSubmit: canSubmit() });

/*@@KIT@@*/
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
    h("button", { class: "btn", text: "Open packet ↗",
      onclick: () => window.open("/packet", "_blank") }));
}

function render() {
  renderBar();
  const app = document.getElementById("app");
  app.textContent = "";
  if (!S.queue) { app.append(h("div", { class: "card dim3", text: "loading…" })); return; }
  if (!S.queue.packet_built) {
    app.append(h("div", { class: "card dim",
      text: "No packet built yet — run `bench review build` first; this surface only ever serves built packet bytes." }));
    return;
  }

  const cap = h("div", { class: "card" });
  cap.append(h("h2", { text: "Capture (strictly before reveal)" }));
  if (!S.sel) cap.append(h("div", { class: "dim3", text: S.queue.pending.length ? "select a pending comparison" : "queue complete — every packet comparison has a verdict" }));
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