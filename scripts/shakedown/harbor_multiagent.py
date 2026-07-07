"""Robust multi-turn A/B: haiku (control) vs sonnet (treatment), openai judge.

Authored + driven in-process through ``harness.sdk`` (refactor 03/08): builds the
MULTI-TURN reference image (``harness.images``, digest-pinned) and runs it through
the harbor engine for a real haiku-vs-sonnet matchup over 2 tasks — both arms
anthropic (both capture reasoning), judged by a third-vendor openai model (no
judge/arm vendor overlap). Confirms the flight recorder captures MULTI-TURN,
agent-attributed reasoning (planner / worker-N draft+revise / critic), runs the
full grade→forensics→judge→analyze pipeline (real openai advisory review +
judge). The harness stands the metering proxy up and tears it down around the run
(``run.config`` ``proxy.managed``) — zero docker calls here.

  $ uv run --env-file .env python scripts/shakedown/harbor_multiagent.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import REPO, Tally, banner, empty_dir, require_keys_or_exit  # noqa: E402
from _scenario import (  # noqa: E402
    ESCALATION, check_egress_attribution, check_harbor_provenance,
    harbor_run_config, holdout_task, print_holdout_grades,
)

from harness.images import build, resolve  # noqa: E402
from harness.run.flight_recorder import resolve_flight_recorder, slice_reasoning_by_agent  # noqa: E402
from harness.sdk import Experiment  # noqa: E402

REF_DIR = REPO / "images" / "reference" / "multi-agent"
CONTROL, TREATMENT = "anthropic/claude-haiku-4-5-20251001", "anthropic/claude-sonnet-4-5-20250929"
JUDGE = "openai/gpt-4.1-mini-2025-04-14"  # third vendor — no judge/arm overlap
ALLOWLIST = ["api.anthropic.com"]
TASKS = {
    "t_math": {"prompt": "Write solution.py defining add(a, b) returning a + b, and is_palindrome(s) "
                         "returning True iff s reads the same forwards and backwards.",
               "holdout": "from solution import add, is_palindrome as p; assert add(2,3)==5 and add(0,0)==0 and p('racecar') and not p('abc')"},
    "t_str": {"prompt": "Write solution.py defining factorial(n) returning n! (with 0!==1), and "
                        "reverse_string(s) returning the string s reversed.",
              "holdout": "from solution import factorial as f, reverse_string as r; assert f(5)==120 and f(0)==1 and r('abc')=='cba'"},
}


def main():
    require_keys_or_exit("ANTHROPIC_API_KEY", "OPENAI_API_KEY", script="harbor_multiagent.py")
    banner("MULTI-TURN A/B — haiku (control) vs sonnet (treatment), openai judge")

    print("building images/reference/multi-agent (multi-turn) ...")
    image = build(resolve(str(REF_DIR))).pinned_ref   # digest-pinned via harness.images

    d = empty_dir("harbor_ma")
    egress_log = d / "metering" / "verdi.jsonl"
    # rubric=None uses the slim SDK judge-rubric template (correctness-first
    # judgment criteria); the verdict-JSON response contract is harness-owned packet
    # framing, supplied on every judge call regardless of rubric [refactor 13 OI-C].
    exp = (Experiment("harbor_ma", seed=11, cost_ceiling_usd=25.0)
           .arm("control", model=CONTROL)
           .arm("treatment", model=TREATMENT)
           .judge(JUDGE, escalation=ESCALATION)
           .corpus("ma-multiturn", "1.0.0").repetitions(1)
           .run_config(harbor_run_config(  # both arms anthropic → single-host allowlist
               egress_log, allowlist=ALLOWLIST,
               keys_by_arm={"control": ["ANTHROPIC_API_KEY"], "treatment": ["ANTHROPIC_API_KEY"]})))
    for tid, t in TASKS.items():
        exp.task(holdout_task(tid, t["prompt"], t["holdout"], image))
    ws = exp.write(d)

    ws.plan(actor="shakedown")
    print("\n--- running REAL multi-turn containers via harbor (planner+draft+revise+critic/trial) ---")
    ws.run(engine="harbor")
    ws.grade(runner="local-exec")                     # EXECUTES the declared holdouts on the real output
    gview = ws.view()
    print_holdout_grades(gview)

    # full pipeline so the operator UI shows grades + forensics + judge
    ws.forensics(model=JUDGE)                         # real openai advisory review over the reasoning
    ws.judge()                                        # real openai judge
    ws.analyze(exploratory=True)

    print("\n[FLIGHT RECORDER — MULTI-TURN, agent-attributed reasoning captured through harbor]")
    view = ws.view()
    multi_turn_ok = False
    for tv in view.trials():
        rec = tv.record
        _s, fr = resolve_flight_recorder(rec["artifacts_path"], tv.flight_recorder_sha)
        by_model = (rec.get("flags") or {}).get("telemetry_by_model")
        if fr is None:
            print(f"\n  {rec['arm']}/{rec['task_id']}: (no recorder)")
            continue
        groups = slice_reasoning_by_agent(fr)
        turns = Counter(e.agent for e in fr.entries)
        if turns.get("worker-1", 0) >= 2 or turns.get("worker-2", 0) >= 2:
            multi_turn_ok = True
        print(f"\n  {rec['arm']}/{rec['task_id']}  roles+turns={dict(turns)}  by_model={ {m: v.get('tokens_out') for m, v in (by_model or {}).items()} }")
        for role in ("planner", "worker-1", "worker-2", "critic", "orchestrator"):
            for j, e in enumerate(groups.get(role, [])):
                print(f"    [{role}:{j}] {e.content.replace(chr(10),' ')[:80]}")

    t = Tally("multi-turn haiku-vs-sonnet harbor")
    check_harbor_provenance(t, view, expected_trials=4)
    t.check("both arms captured reasoning (both anthropic)",
            all(tv.flight_recorder_sha for tv in view.trials()))
    t.check("reasoning is MULTI-TURN (worker draft+revise)", multi_turn_ok)
    t.check("forensics + judge ledgered (real openai)",
            bool(view.by_kind("forensics_report")) and bool(view.by_kind("judge_verdict")))
    check_egress_attribution(t, egress_log, view, ALLOWLIST)
    t.finish()


if __name__ == "__main__":
    main()
