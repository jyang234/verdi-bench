#!/usr/bin/env python3
"""A worked MULTI-AGENT, MULTI-TURN reference trial agent for the verdi harbor path [refactor 03 §3].

Demonstrates how a workflow agent stack reports its own sub-structure to verdi.
An orchestrator runs an ITERATIVE team — all inside ONE container, ONE image, ONE
trial (the whole workflow is the agent's internal business):

    planner ──▶ worker-1 (draft → revise) ──▶ worker-2 (draft → revise) ──▶ critic ──▶ orchestrator

Each sub-agent turn contributes a step, so verdi sees the real trajectory: the
planner's decomposition, each worker's DRAFT then its REVISE (multiple reasoning
entries per sub-agent — the iteration is visible), a critic's review, and the
orchestrator's CLOSING REPORT — a deterministic (unmetered, honest-null tokens)
final statement of what was delivered, assembled from which workers' outputs,
that the critic's note was recorded but not auto-applied, and the real exit code
of an import smoke check it actually runs. It emits ``artifacts/agent_log.json``
in the verdi generic v2 format:

  * ``reasoning``  — a per-turn ``agent`` role [EVAL-24 AC-6]: planner, worker-N
                     (twice: draft + revise), critic.
  * ``trajectory`` — a per-turn ``agent`` role [EVAL-21] AND the turn's RESPONSE
                     in each step's ``detail`` (the code a worker wrote, the plan,
                     the critique) — so actions and outputs are legible, not just
                     "a file was edited".
  * ``telemetry_by_model`` — per-model spend summed across all the turns.

Extends ``verdi-base``: the workspace layout, the ``/verdi/request.json`` reader,
the CONNECT-tunnel + ``Proxy-Authorization`` egress dance, and the generic-log
writer all come from ``verdi_agent`` (stdlib-only, already on the base's
PYTHONPATH), so this file is multi-agent ORCHESTRATION logic only — the tunnel and
v2-log plumbing it used to hand-roll are gone. Fail-visible: any error still writes
an agent_log (absent-honest) so the trial is scorable. The PURE ``build_agent_log``
is import-safe so verdi validates the emitted shape deterministically — see
tests/test_eval24_multi_agent_reference.py.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time

from verdi_agent import ARTIFACTS, WORKSPACE, AgentLog, post_json, read_request

PLANNER_SYS = ("You are the PLANNER of a multi-agent coding team. Break the task into 2 "
               "concrete sub-tasks, one per line, no prose.")
WORKER_SYS = "You are a WORKER. Output only the Python code for this sub-task, no prose."
REVISE_SYS = ("You are a WORKER revising your OWN draft. Check it for bugs and edge cases, "
              "then output ONLY the improved Python code, no prose.")
CRITIC_SYS = ("You are the CRITIC. In one or two sentences, note any remaining bug or risk "
              "in the proposed solution.")


def call_model(system, prompt, model):
    """One sub-agent turn → (reasoning, text, tokens_out). Anthropic models expose
    extended-thinking reasoning (budget_tokens < max_tokens); others return none.
    Egress rides ``verdi_agent.post_json`` — the metering proxy + per-trial
    credential on CONNECT."""
    bare = model.split("/", 1)[-1]
    if model.startswith("anthropic/"):
        r = post_json("api.anthropic.com", "/v1/messages",
                      {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                      {"model": bare, "max_tokens": 2048,
                       "thinking": {"type": "enabled", "budget_tokens": 1024},
                       "system": system, "messages": [{"role": "user", "content": prompt}]})
        reasoning = "".join(b.get("thinking", "") for b in r["content"] if b.get("type") == "thinking")
        text = "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
        return reasoning, text, (r.get("usage", {}) or {}).get("output_tokens")
    r = post_json("api.openai.com", "/v1/chat/completions",
                  {"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                  {"model": bare, "max_tokens": 1024, "messages": [
                      {"role": "system", "content": system}, {"role": "user", "content": prompt}]})
    return "", r["choices"][0]["message"]["content"], (r.get("usage", {}) or {}).get("completion_tokens")


def _strip_fences(text):
    m = re.search(r"```(?:python)?\n(.*?)```", (text or "").strip(), re.DOTALL)
    return (m.group(1) if m else text or "").strip()


def _record_turn(log, i, t):
    """Append turn ``i``'s reasoning span (if any) + its trajectory step onto
    ``log`` [EVAL-21 + EVAL-24]. The step carries the turn's RESPONSE in ``detail``;
    the turn's measured tokens ride the reasoning entry (and telemetry), never the
    step. Each reasoning entry declares ``turn: i`` — its own step's index — so
    verdi interleaves thought and action into ONE process timeline [flight-recorder
    charter]. A worker's draft and revise are two entries under the same role, so
    the iteration is visible; a metered turn is legible against the unmetered
    orchestrator (which reports no tokens — honest absence)."""
    role = t["agent"]
    if t.get("reasoning") is not None:
        log.reasoning(t["reasoning"] or "(no native reasoning)", agent=role, turn=i,
                      tokens=t.get("tokens"), relative_ts=t.get("ts"))
    kind, ts = t.get("kind", "message"), t.get("ts")
    if kind == "file_edit":
        log.file_edit(t.get("files") or [], detail=t.get("detail"), agent=role, relative_ts=ts)
    elif kind == "test_run":
        log.test_run(t.get("command"), detail=t.get("detail"), exit_code=t.get("exit_code"),
                     agent=role, relative_ts=ts)
    else:
        log.message(t.get("detail"), agent=role, relative_ts=ts)


def build_agent_log(*, model, turns, totals=None):
    """PURE: the verdi generic v2 log from an ordered list of sub-agent TURNS
    [EVAL-21 + EVAL-24]. Each turn is a dict:
      {agent, reasoning?, kind, detail?, files?, command?, exit_code?, tokens?,
       model?, ts?}
    ``ts`` is the turn's measured seconds since trial start; it rides both the
    trajectory step (relative_ts) and the reasoning entry (v3 linkage), and each
    reasoning entry declares ``turn: i`` — its own step's index.

    The generic-format assembly (v2 shape, per-step/entry construction, version
    selection) is ``verdi_agent.AgentLog``'s job now; this only MAPS this workflow's
    turns onto it and sums the per-model / whole-trial telemetry (the whole-trial
    ``telemetry`` block stays the sole authoritative stream — honest-null tokens
    when nothing was measured). It stays PURE (AgentLog's no-I/O ``_build``, the
    seam's test-reuse builder) so the eval24 test can validate the emitted shape
    without docker or real keys."""
    log = AgentLog()
    for i, t in enumerate(turns):
        _record_turn(log, i, t)
    doc = log._build()
    by_model: dict[str, dict] = {}
    for t in turns:
        tok = t.get("tokens")
        if tok is not None:
            by_model.setdefault(t.get("model") or model, {"tokens_out": 0})["tokens_out"] += tok
    if totals is None:
        total = sum(t["tokens"] for t in turns if t.get("tokens") is not None)
        totals = {"tokens_out": total or None}
    doc["telemetry"] = totals
    if by_model:
        doc["telemetry_by_model"] = by_model
    return doc


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    req = read_request()
    prompt, model = req.prompt, req.model
    turns: list[dict] = []
    t0 = time.monotonic()

    def ts() -> float:
        """Measured seconds since trial start — real timing, stamped per turn."""
        return round(time.monotonic() - t0, 1)

    try:
        # PLANNER — decompose the task into sub-tasks
        p_reason, p_text, p_tok = call_model(PLANNER_SYS, prompt, model)
        subtasks = [s.strip("-* ").strip() for s in p_text.splitlines() if s.strip()][:2] or [prompt]
        turns.append({"agent": "planner", "reasoning": p_reason, "kind": "message",
                      "detail": "plan: " + " | ".join(subtasks), "tokens": p_tok,
                      "model": model, "ts": ts()})

        # WORKERS — each drafts, then revises its own draft (multi-turn)
        code_parts = []
        for i, sub in enumerate(subtasks, 1):
            role = f"worker-{i}"
            d_reason, d_text, d_tok = call_model(WORKER_SYS, sub, model)
            draft = _strip_fences(d_text)
            turns.append({"agent": role, "reasoning": d_reason, "kind": "file_edit",
                          "detail": draft, "files": ["solution.py"], "tokens": d_tok,
                          "model": model, "ts": ts()})
            r_reason, r_text, r_tok = call_model(
                REVISE_SYS, f"Sub-task: {sub}\n\nYour draft:\n{draft}", model)
            revised = _strip_fences(r_text)
            turns.append({"agent": role, "reasoning": r_reason, "kind": "file_edit",
                          "detail": revised, "files": ["solution.py"], "tokens": r_tok,
                          "model": model, "ts": ts()})
            code_parts.append(revised)

        # CRITIC — review the combined solution (the "where could it go wrong" turn)
        combined = "\n\n".join(code_parts)
        c_reason, c_text, c_tok = call_model(
            CRITIC_SYS, f"Task: {prompt}\n\nProposed solution:\n{combined}", model)
        turns.append({"agent": "critic", "reasoning": c_reason, "kind": "message",
                      "detail": (c_text or "").strip()[:400], "tokens": c_tok,
                      "model": model, "ts": ts()})

        # ORCHESTRATOR — write the aggregated solution, then CLOSE the workflow
        # with a complete deterministic report: what was delivered, assembled
        # from which turns, what happened to the critique, and the real exit
        # code of an import smoke check it actually runs. No model call — the
        # closing statement is the workflow's own truthful bookkeeping.
        (WORKSPACE / "solution.py").write_text(combined + "\n", encoding="utf-8")
        check = subprocess.run(
            [sys.executable, "-c", "import solution"], cwd=str(WORKSPACE), timeout=60,
            capture_output=True, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},  # no __pycache__ in the graded diff
        )
        defs = re.findall(r"(?m)^def\s+(\w+)", combined)
        turns.append({
            "agent": "orchestrator",
            "reasoning": (
                f"final deliverable: solution.py ({len(combined.splitlines())} lines"
                + (f", defining {', '.join(defs)}" if defs else "")
                + f") assembled from {len(code_parts)} workers' revised outputs "
                + f"({len(turns) - 1} model turns total); critic note recorded above, "
                + f"not auto-applied; import smoke check exit {check.returncode}"
            ),
            "kind": "test_run", "command": f"{pathlib.Path(sys.executable).name} -c 'import solution'",
            "detail": (check.stderr.strip() or "import ok"), "exit_code": check.returncode,
            "ts": ts(),
        })
        log = build_agent_log(model=model, turns=turns)
    except Exception as e:  # fail-visible: an absent-honest log still makes the trial scorable
        turns.append({"agent": "orchestrator", "reasoning": f"workflow failed: {e}",
                      "kind": "message", "ts": ts()})
        (ARTIFACTS / "agent_log.json").write_text(
            json.dumps(build_agent_log(model=model, turns=turns)), encoding="utf-8")
        raise
    (ARTIFACTS / "agent_log.json").write_text(json.dumps(log), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
