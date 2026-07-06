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

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, empty_dir  # noqa: E402

from harness.grade.holdouts import AssertionHoldout  # noqa: E402
from harness.images import build, resolve  # noqa: E402
from harness.sdk import Experiment, MissingEnvKeysError, Task, require_env_keys  # noqa: E402

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
    try:
        require_env_keys("ANTHROPIC_API_KEY", "OPENAI_API_KEY")   # L6 needs both
    except MissingEnvKeysError as e:
        raise SystemExit(f"{e}\nrun: uv run --env-file .env python scripts/shakedown/harbor.py")
    print("=" * 72, "\nL6 — real-agent harbor run (real LLMs in real containers)\n" + "=" * 72)

    print("building images/official/generic-llm (single-turn) ...")
    image = build(resolve("generic-llm")).pinned_ref   # digest-pinned via harness.images

    d = empty_dir("harbor")
    egress_log = d / "metering" / "verdi.jsonl"
    exp = (Experiment("harbor", seed=1234, cost_ceiling_usd=10.0)
           .arm("treatment", model="openai/gpt-4.1-mini-2025-04-14")
           .arm("control", model="anthropic/claude-haiku-4-5-20251001")
           .judge("fake/deterministic-2026-01-01", rubric="Judge on correctness.\n",
                  escalation={"kappa_threshold": 0.6, "min_human_verdicts": 1})
           .corpus("harbor-mini", "1.0.0").repetitions(1)
           .run_config({  # the harness stands up + tears down the metering proxy
               "proxy": {"managed": True, "allowlist": ["api.openai.com", "api.anthropic.com"],
                         "log_path": str(egress_log)},
               "provider_key_names_by_arm": {"treatment": ["OPENAI_API_KEY"],
                                             "control": ["ANTHROPIC_API_KEY"]}}))
    for tid, prompt in PROMPTS.items():
        exp.task(Task(tid, prompt=prompt, image=image, task_class="feature",
                      holdout=AssertionHoldout(expression=HOLDOUTS[tid])))
    ws = exp.write(d)

    ws.plan(actor="shakedown")
    print("\n--- running REAL containers via harbor (real LLM API calls, managed proxy) ---")
    ws.run(engine="harbor")
    ws.grade(runner="local-exec")            # EXECUTES the declared holdouts on the real output
    view = ws.view()
    grades = view.latest_grade_by_trial()
    for tv in view.trials():
        rec = tv.record
        passed = bool(grades.get(rec["trial_id"], {}).get("binary_score"))
        print(f"    {rec['arm']:9s} {rec['task_id']:6s} -> {'PASS' if passed else 'FAIL'}")
    ws.judge()
    ws.analyze(exploratory=True)
    chain = ws.verify_chain()

    trials = [tv.record for tv in view.trials()]
    egress: dict = {}
    for line in egress_log.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        egress.setdefault(r["trial"], set()).add(f"{r['host']}:{r['decision']}")
    digests = {str(rec.get("provenance", {}).get("image_digest", "")).startswith("sha256:") for rec in trials}
    t = Tally("L6 real-agent harbor")
    t.check("real trials completed", len(trials) == 4 and all(
        rec.get("provenance", {}).get("engine") == "harbor" for rec in trials), f"{len(trials)} harbor trials")
    t.check("images digest-pinned", digests == {True}, "provenance image_digest is sha256:")
    t.check("per-trial egress attributed", len(egress) == len(trials)
            and all(any("allow" in h for h in hs) for hs in egress.values()),
            f"{len(egress)} trials attributed through the metering proxy")
    t.check("chain verifies", chain.chain_ok, "chain OK")
    t.finish()


if __name__ == "__main__":
    main()
