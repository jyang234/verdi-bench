"""L6 — real-agent harbor run (opt-in: needs Docker + ANTHROPIC_API_KEY + OPENAI_API_KEY).

Authored + driven in-process through ``harness.sdk`` (refactor 03/08): builds the
official ``generic-llm`` trial image (``harness.images``, digest-pinned), runs two
real models (openai treatment vs anthropic control) solving coding tasks in
hermetic containers via the harbor engine, grades their REAL output by EXECUTING
the declared holdouts (``--runner local-exec``, ADVISORY), judges + analyzes, and
checks per-trial egress attribution. The harness stands the metering proxy up and
tears it down around the run (``run.config`` ``proxy.managed``) — zero docker calls
here.

  $ uv run --env-file .env python scripts/shakedown/harbor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, banner, empty_dir, require_keys_or_exit  # noqa: E402
from _scenario import (  # noqa: E402
    ESCALATION, FAKE_JUDGE, check_egress_attribution, check_harbor_provenance,
    harbor_run_config, holdout_task, print_holdout_grades,
)

from harness.images import build, resolve  # noqa: E402
from harness.sdk import Experiment  # noqa: E402

# Allowed egress hosts — one list feeds BOTH the run config and the egress check,
# so "allowed" and "checked" cannot drift.
ALLOWLIST = ["api.openai.com", "api.anthropic.com"]

# The declared holdouts — the same assertions the hand-rolled HOLDOUTS dict ran,
# now first-class AssertionHoldouts executed against each trial's real solution.py.
HOLDOUTS = {
    "t_add": "from solution import add; assert add(2,3)==5 and add(-1,1)==0 and add(0,0)==0",
    "t_pal": "from solution import is_palindrome as p; assert p('racecar') and p('abba') and not p('abc')",
}
PROMPTS = {
    "t_add": "Write solution.py defining a function add(a, b) that returns the sum a + b.",
    "t_pal": "Write solution.py defining a function is_palindrome(s) returning True if s reads "
             "the same forwards and backwards, else False.",
}


def main():
    require_keys_or_exit("ANTHROPIC_API_KEY", "OPENAI_API_KEY", script="harbor.py")
    banner("L6 — real-agent harbor run (real LLMs in real containers)")

    print("building images/official/generic-llm (single-turn) ...")
    image = build(resolve("generic-llm")).pinned_ref   # digest-pinned via harness.images

    d = empty_dir("harbor")
    egress_log = d / "metering" / "verdi.jsonl"
    exp = (Experiment("harbor", seed=1234, cost_ceiling_usd=10.0)
           .arm("treatment", model="openai/gpt-4.1-mini-2025-04-14")
           .arm("control", model="anthropic/claude-haiku-4-5-20251001")
           .judge(FAKE_JUDGE, rubric="Judge on correctness.\n", escalation=ESCALATION)
           .corpus("harbor-mini", "1.0.0").repetitions(1)
           .run_config(harbor_run_config(  # the harness stands up + tears down the metering proxy
               egress_log, allowlist=ALLOWLIST,
               keys_by_arm={"treatment": ["OPENAI_API_KEY"], "control": ["ANTHROPIC_API_KEY"]})))
    for tid in PROMPTS:
        exp.task(holdout_task(tid, PROMPTS[tid], HOLDOUTS[tid], image))
    ws = exp.write(d)

    ws.plan(actor="shakedown")
    print("\n--- running REAL containers via harbor (real LLM API calls, managed proxy) ---")
    ws.run(engine="harbor")
    ws.grade(runner="local-exec")            # EXECUTES the declared holdouts on the real output
    view = ws.view()
    print_holdout_grades(view)
    ws.judge()
    ws.analyze(exploratory=True)
    chain = ws.verify_chain()

    t = Tally("L6 real-agent harbor")
    check_harbor_provenance(t, view, expected_trials=4)
    check_egress_attribution(t, egress_log, view, ALLOWLIST)
    t.check("chain verifies", chain.chain_ok, "chain OK")
    t.finish()


if __name__ == "__main__":
    main()
