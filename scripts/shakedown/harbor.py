"""L6 — real-agent harbor run (opt-in: needs Docker + ANTHROPIC_API_KEY + OPENAI_API_KEY).

Builds a real LLM trial-agent image, stands up a minimal metering CONNECT proxy,
runs two real models (openai treatment vs anthropic control) solving coding tasks
in digest-pinned hermetic containers via `bench run --engine harbor`, grades their
real output, and checks per-trial egress attribution. Tears the proxy down after.

  $ uv run --env-file .env python scripts/shakedown/harbor.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ASSETS, Tally, bench, dump_yaml, empty_dir, event_counts, events  # noqa: E402

IMAGE = "verdi-shakedown-agent:latest"
PROXY, NET_METERED, NET_EGRESS = "verdi-metering-proxy", "verdi-metered", "verdi-egress"
HOLDOUTS = {
    "t_add": "from solution import add; assert add(2,3)==5 and add(-1,1)==0 and add(0,0)==0",
    "t_pal": "from solution import is_palindrome as p; assert p('racecar') and p('abba') and not p('abc')",
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
    r = subprocess.run([sys.executable, "-c", HOLDOUTS[task_id]], cwd=str(ws),
                       capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def main():
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if not os.environ.get(k):
            raise SystemExit(f"{k} not set — L6 needs both. "
                             "Run: uv run --env-file .env python scripts/shakedown/harbor.py")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        raise SystemExit("docker daemon not reachable — L6 needs Docker.")
    print("=" * 72, "\nL6 — real-agent harbor run (real LLMs in real containers)\n" + "=" * 72)

    print("building the trial-agent image ...")
    sh("build", "-q", "-t", IMAGE, str(ASSETS / "harbor"))

    d = empty_dir("harbor")
    logdir = d / "proxylog"
    dump_yaml(d / "experiment.yaml", {
        "arms": [{"name": "treatment", "platform": "generic", "model": "openai/gpt-4.1-mini-2025-04-14", "payload": {}},
                 {"name": "control", "platform": "generic", "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}}],
        "corpus": {"id": "harbor-mini", "version": "1.0.0"}, "repetitions": 1,
        "primary_metric": "holdout_pass_rate", "decision_rule": "delta_holdout_pass_rate > 0",
        "judge": {"model": "fake/deterministic-2026-01-01", "rubric": "rubric.md", "orders": "both",
                  "temperature": 0, "escalation": {"kappa_threshold": 0.6, "min_human_verdicts": 1}},
        "seed": 1234, "cost_ceiling": {"amount": 10.0, "currency": "USD"}})
    dump_yaml(d / "tasks.yaml", {"tasks": [
        {"id": "t_add", "image": IMAGE, "task_class": "feature",
         "prompt": "Write solution.py defining a function add(a, b) that returns the sum a + b."},
        {"id": "t_pal", "image": IMAGE, "task_class": "feature",
         "prompt": "Write solution.py defining a function is_palindrome(s) returning True if s reads the same forwards and backwards, else False."}]})
    dump_yaml(d / "run.config.yaml", {
        "proxy": {"url": f"http://{PROXY}:3128", "log_path": str(logdir / "verdi.jsonl"),
                  "allowlist": ["api.anthropic.com", "api.openai.com"]},
        "provider_key_names_by_arm": {"treatment": ["OPENAI_API_KEY"], "control": ["ANTHROPIC_API_KEY"]}})
    (d / "rubric.md").write_text("Judge on correctness.\n", encoding="utf-8")
    led = d / "ledger.ndjson"

    try:
        proxy_up(logdir)
        bench("plan", d / "experiment.yaml", "--ledger", led)
        print("\n--- running REAL containers via harbor (real LLM API calls) ---")
        bench("run", d, "--engine", "harbor")
        trials = [e["trial_record"] for e in events(led, "trial")]
        for rec in trials:
            ws = Path(rec["artifacts_path"]).parent
            sol = ws / "solution.py"
            passed = sol.exists() and run_holdout(ws, rec["task_id"])
            (ws / "holdout_results.json").write_text(
                json.dumps({"assertions": [{"id": "h1", "result": "pass" if passed else "fail"}]}), encoding="utf-8")
            print(f"    {rec['arm']:9s} {rec['task_id']:6s} -> {'PASS' if passed else 'FAIL'}")
        bench("grade", d, "--runner", "local")
        bench("judge", d)
        bench("analyze", d, "--exploratory")
        chain = bench("verify-chain", led, check=False)

        egress = {}
        for line in (logdir / "verdi.jsonl").read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:
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
        t.check("chain verifies", chain.returncode == 0, "chain OK")
        t.finish()
    finally:
        proxy_down()
        print("(metering proxy + networks torn down)")


if __name__ == "__main__":
    main()
