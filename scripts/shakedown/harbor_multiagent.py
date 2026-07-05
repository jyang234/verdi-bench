"""Robust multi-turn A/B: haiku (control) vs sonnet (treatment), openai judge.

Builds the MULTI-TURN reference image and runs it through harbor for a real
haiku-vs-sonnet matchup over 2 tasks — both arms anthropic (both capture
reasoning), judged by a third-vendor openai model (no judge/arm vendor overlap).
Confirms the flight recorder captures MULTI-TURN, agent-attributed reasoning
(planner / worker-N draft+revise / critic), runs the full grade→forensics→judge
→analyze pipeline (real openai advisory review + judge), and tears down.

  $ uv run --env-file .env python scratchpad/shakedown/harbor_multiagent.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ASSETS, REPO, Tally, bench, dump_yaml, empty_dir, events  # noqa: E402

from harness.run.flight_recorder import resolve_flight_recorder, slice_reasoning_by_agent  # noqa: E402

IMAGE = "verdi/multi-agent-reference:local"
REF_DIR = REPO / "images" / "multi-agent-reference"
# NET_METERED MUST be harbor's METERED_NETWORK constant ("verdi-metered") — the
# engine joins the trial container to it, so the proxy must live there too.
PROXY, NET_METERED, NET_EGRESS = "verdi-ma-proxy", "verdi-metered", "verdi-ma-egress"
CONTROL, TREATMENT = "anthropic/claude-haiku-4-5-20251001", "anthropic/claude-sonnet-4-5-20250929"
JUDGE = "openai/gpt-4.1-mini-2025-04-14"  # third vendor — no judge/arm overlap
TASKS = {
    "t_math": {"prompt": "Write solution.py defining add(a, b) returning a + b, and is_palindrome(s) "
                         "returning True iff s reads the same forwards and backwards.",
               "holdout": "from solution import add, is_palindrome as p; assert add(2,3)==5 and add(0,0)==0 and p('racecar') and not p('abc')"},
    "t_str": {"prompt": "Write solution.py defining factorial(n) returning n! (with 0!==1), and "
                        "reverse_string(s) returning the string s reversed.",
              "holdout": "from solution import factorial as f, reverse_string as r; assert f(5)==120 and f(0)==1 and r('abc')=='cba'"},
}


def sh(*args, check=True):
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def proxy_up(logdir: Path):
    sh("network", "create", "--internal", NET_METERED, check=False)
    sh("network", "create", NET_EGRESS, check=False)
    sh("rm", "-f", PROXY, check=False)
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / "verdi.jsonl").write_text("", encoding="utf-8")
    sh("run", "-d", "--name", PROXY, "--network", NET_METERED,
       "-v", f"{ASSETS / 'harbor' / 'proxy.py'}:/proxy.py:ro", "-v", f"{logdir}:/var/log/verdi",
       "python:3.12-alpine", "python", "/proxy.py")
    sh("network", "connect", NET_EGRESS, PROXY)
    subprocess.run(["sleep", "2"])


def proxy_down():
    sh("rm", "-f", PROXY, check=False)
    sh("network", "rm", NET_METERED, NET_EGRESS, check=False)


def run_holdout(ws: Path, task_id: str) -> bool:
    try:
        r = subprocess.run([sys.executable, "-c", TASKS[task_id]["holdout"]], cwd=str(ws),
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def main():
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if not os.environ.get(k):
            raise SystemExit(f"{k} not set — run via: uv run --env-file .env python <this>")
    print("=" * 74, "\nMULTI-TURN A/B — haiku (control) vs sonnet (treatment), openai judge\n" + "=" * 74)
    print("building images/multi-agent-reference (multi-turn) ...")
    sh("build", "-q", "-t", IMAGE, str(REF_DIR))

    d = empty_dir("harbor_ma")
    logdir = d / "proxylog"
    dump_yaml(d / "experiment.yaml", {
        "arms": [{"name": "control", "platform": "generic", "model": CONTROL, "payload": {}},
                 {"name": "treatment", "platform": "generic", "model": TREATMENT, "payload": {}}],
        "corpus": {"id": "ma-multiturn", "version": "1.0.0"}, "repetitions": 1,
        "primary_metric": "holdout_pass_rate", "decision_rule": "delta_holdout_pass_rate > 0",
        "judge": {"model": JUDGE, "rubric": "rubric.md", "orders": "both",
                  "temperature": 0, "escalation": {"kappa_threshold": 0.6, "min_human_verdicts": 1}},
        "seed": 11, "cost_ceiling": {"amount": 25.0, "currency": "USD"}})
    dump_yaml(d / "tasks.yaml", {"tasks": [
        {"id": tid, "image": IMAGE, "task_class": "feature", "prompt": t["prompt"]}
        for tid, t in TASKS.items()]})
    dump_yaml(d / "run.config.yaml", {
        "proxy": {"url": f"http://{PROXY}:3128", "log_path": str(logdir / "verdi.jsonl"),
                  "allowlist": ["api.anthropic.com"]},  # both arms are anthropic
        "provider_key_names_by_arm": {"control": ["ANTHROPIC_API_KEY"], "treatment": ["ANTHROPIC_API_KEY"]}})
    (d / "rubric.md").write_text(
        "You are comparing two responses to a coding task. Judge correctness first: "
        "prefer the response whose holdout tests pass; if holdout results are identical, "
        "judge code quality and robustness; if the responses are identical, return TIE.\n\n"
        "Respond with EXACTLY ONE JSON object and nothing else — no prose, no markdown fences:\n"
        '{"winner": "1" | "2" | "TIE" | "CANT_JUDGE", "reason": "<one short sentence>", '
        '"evidence": [{"kind": "holdout", "response": 1, "ref": "<assertion id such as h1>"}], '
        '"confidence": <number between 0 and 1>}\n\n'
        'If winner is "1" or "2", evidence MUST cite a locator '
        '(holdout: {"kind":"holdout","response":<1|2>,"ref":"<id>"}; '
        'diff: {"kind":"diff","response":<1|2>,"hunk":"<a diff line>"}). '
        "TIE/CANT_JUDGE need no evidence. Output the JSON object only.\n", encoding="utf-8")
    led = d / "ledger.ndjson"

    try:
        proxy_up(logdir)
        bench("plan", d / "experiment.yaml", "--ledger", led)
        print("\n--- running REAL multi-turn containers via harbor (planner+draft+revise+critic/trial) ---")
        bench("run", d, "--engine", "harbor")
        for ev in events(led, "trial"):
            rec = ev["trial_record"]
            ws = Path(rec["artifacts_path"]).parent
            passed = (ws / "solution.py").exists() and run_holdout(ws, rec["task_id"])
            (ws / "holdout_results.json").write_text(
                json.dumps({"assertions": [{"id": "h1", "result": "pass" if passed else "fail"}]}), encoding="utf-8")
            print(f"    {rec['arm']:9s} {rec['task_id']:7s} holdout -> {'PASS' if passed else 'FAIL'}")

        # full pipeline so the operator UI shows grades + forensics + judge
        bench("grade", d, "--runner", "local")
        bench("forensics", "scan", d, "--model", JUDGE)   # real openai advisory review over the reasoning
        bench("judge", d)                                 # real openai judge
        bench("analyze", d, "--exploratory")

        print("\n[FLIGHT RECORDER — MULTI-TURN, agent-attributed reasoning captured through harbor]")
        multi_turn_ok = False
        for ev in events(led, "trial"):
            rec = ev["trial_record"]
            _s, fr = resolve_flight_recorder(rec["artifacts_path"], ev.get("flight_recorder_sha"))
            by_model = (rec.get("flags") or {}).get("telemetry_by_model")
            if fr is None:
                print(f"\n  {rec['arm']}/{rec['task_id']}: (no recorder)")
                continue
            groups = slice_reasoning_by_agent(fr)
            turns = Counter(e.agent for e in fr.entries)
            if turns.get("worker-1", 0) >= 2 or turns.get("worker-2", 0) >= 2:
                multi_turn_ok = True
            print(f"\n  {rec['arm']}/{rec['task_id']}  roles+turns={dict(turns)}  by_model={ {m: v.get('tokens_out') for m, v in (by_model or {}).items()} }")
            for role in ("planner", "worker-1", "worker-2", "critic"):
                for j, e in enumerate(groups.get(role, [])):
                    print(f"    [{role}:{j}] {e.content.replace(chr(10),' ')[:80]}")

        digests = {str(ev["trial_record"].get("provenance", {}).get("image_digest", "")).startswith("sha256:")
                   for ev in events(led, "trial")}
        n = len(events(led, "trial"))
        t = Tally("multi-turn haiku-vs-sonnet harbor")
        t.check("real harbor trials completed", n == 4 and all(
            ev["trial_record"].get("provenance", {}).get("engine") == "harbor" for ev in events(led, "trial")))
        t.check("images digest-pinned", digests == {True})
        t.check("both arms captured reasoning (both anthropic)",
                all(ev.get("flight_recorder_sha") for ev in events(led, "trial")))
        t.check("reasoning is MULTI-TURN (worker draft+revise)", multi_turn_ok)
        t.check("forensics + judge ledgered (real openai)",
                bool(events(led, "forensics_report")) and bool(events(led, "judge_verdict")))
        t.finish()
    finally:
        proxy_down()
        print("(proxy + networks torn down)")


if __name__ == "__main__":
    main()
