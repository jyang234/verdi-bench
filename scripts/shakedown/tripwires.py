"""L3 — the tripwire matrix: 18 adversarial vectors, every fence must fire (hermetic).

Grouped: pre-registration refusals, ledger tamper, analyze fence, cost/insulation/
stats, gaming detection, asymmetric contamination. No keys, no Docker (fake judge,
fake/ arm models). Exits nonzero unless all 18 fire as designed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import (ASSETS, Tally, bench, dump_yaml, empty_dir, event_counts,  # noqa: E402
                      events, inject_grades, load_yaml)

BASE = load_yaml(ASSETS / "golden" / "experiment.yaml")
TASKS = load_yaml(ASSETS / "golden" / "tasks.yaml")["tasks"]
FAKE_JUDGE = {"model": "fake/deterministic-2026-01-01", "rubric": "rubric.md", "orders": "both",
              "temperature": 0, "escalation": {"kappa_threshold": 0.6, "min_human_verdicts": 1}}
TREATMENT_PASS = {"t1", "t2", "t3", "t4", "t5", "t6"}
CONTROL_PASS = {"t1", "t2"}


def write_exp(d, spec, tasks, rubric="Judge on correctness.\n"):
    dump_yaml(d / "experiment.yaml", spec)
    dump_yaml(d / "tasks.yaml", {"tasks": tasks})
    (d / "rubric.md").write_text(rubric, encoding="utf-8")


def manifest(path, corpus_id="shakedown-mini", semver="1.0.0", tasks=TASKS):
    Path(path).write_text(json.dumps({
        "corpus_id": corpus_id, "semver": semver, "kind": "public",
        "tasks": [{"task_id": t["id"], "sha": hashlib.sha256(t["id"].encode()).hexdigest(),
                   "status": "admitted", "metadata": {"category": t.get("task_class", "misc")}}
                  for t in tasks]}, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- pre-registration
def plan_tripwires(t):
    def run(name, mutate, expect):
        d = empty_dir(f"tw/{name}")
        spec = deepcopy(BASE)
        spec["judge"] = dict(FAKE_JUDGE)
        mutate(spec)
        write_exp(d, spec, TASKS)
        r = bench("plan", d / "experiment.yaml", "--ledger", d / "ledger.ndjson", check=False)
        out = (r.stdout or "") + (r.stderr or "")
        locked = (d / "ledger.ndjson").exists() and events(d / "ledger.ndjson", "experiment_locked")
        t.check(name, r.returncode != 0 and expect.lower() in out.lower() and not locked,
                f"exit {r.returncode}, nothing locked")

    run("missing_cost_ceiling", lambda s: s.pop("cost_ceiling"), "must declare a cost_ceiling")
    run("ineligible_primary_metric", lambda s: s.update(primary_metric="planning_quality"),
        "composite and unknown metrics are banned")
    run("alias_judge_model", lambda s: s["judge"].update(model="anthropic/claude-sonnet-5"),
        "alias ids are rejected")
    run("equality_decision_rule", lambda s: s.update(decision_rule="delta_holdout_pass_rate == 0"),
        "equality on a bootstrap float")
    run("duplicate_arm_names", lambda s: s["arms"][1].update(name=s["arms"][0]["name"]),
        "arm names must be unique")
    run("extra_top_level_key", lambda s: s.update(surprise="p-hack"), "Extra inputs are not permitted")
    run("single_arm", lambda s: s.update(arms=[s["arms"][0]]), "at least 2 items")


# ---------------------------------------------------------------- ledger integrity
def ledger_tripwires(t):
    d = empty_dir("tw/ledger_tamper")
    spec = deepcopy(BASE); spec["judge"] = dict(FAKE_JUDGE)
    write_exp(d, spec, TASKS)
    led = d / "ledger.ndjson"
    bench("plan", d / "experiment.yaml", "--ledger", led)
    bench("run", d)
    clean_ok = bench("verify-chain", led, check=False).returncode == 0
    lines = led.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])  # a NON-head line
    obj["provenance"]["actor"] = obj["provenance"].get("actor", "x") + "_TAMPERED"
    lines[1] = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    led.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = bench("verify-chain", led, check=False)
    t.check("ledger_tamper_detected",
            clean_ok and r.returncode != 0 and "chain broken" in ((r.stdout or "") + (r.stderr or "")).lower(),
            "clean OK then tamper -> CHAIN BROKEN")
    ra = bench("anchor", led, "--out", d / "anchors.ndjson", check=False)
    t.check("anchor_refuses_tampered", ra.returncode != 0 and not (d / "anchors.ndjson").exists(),
            "refused; no anchor written")


# ---------------------------------------------------------------- analyze fence
def build_ready(name, selfcheck=True):
    d = empty_dir(f"tw/{name}")
    spec = deepcopy(BASE); spec["judge"] = dict(FAKE_JUDGE)
    write_exp(d, spec, TASKS)
    m = d / "manifest.json"; manifest(m)
    led = d / "ledger.ndjson"
    bench("plan", d / "experiment.yaml", "--ledger", led)
    bench("run", d)
    inject_grades(led, lambda arm, task: task in (TREATMENT_PASS if arm == "treatment" else CONTROL_PASS))
    bench("grade", d, "--runner", "local")
    bench("judge", d)
    bench("corpus", "calibrate", d, "--manifest", m, "--kind", "full")
    if selfcheck:
        bench("selfcheck", d)
    return d, m


def cant_reason(d):
    evs = events(d / "ledger.ndjson", "cant_analyze")
    return evs[-1].get("reason") if evs else None


def fence_tripwires(t):
    d, m = build_ready("official_before_selfcheck", selfcheck=False)
    bench("analyze", d, "--official", "--corpus", m, check=False)
    t.check("official_before_selfcheck", cant_reason(d) == "selfcheck_required"
            and not (d / "findings.official.md").exists(), "cant_analyze: selfcheck_required")

    d, m = build_ready("quarantine_stale", selfcheck=True)
    bench("analyze", d, "--official", "--corpus", m)  # baseline PASS
    passed_first = (d / "findings.official.md").exists()
    tid = events(d / "ledger.ndjson", "trial")[0]["trial_record"]["trial_id"]
    bench("forensics", "quarantine", d, "--trial-id", tid, "--reason", "shakedown: confirmed tamper")
    (d / "findings.official.md").unlink(missing_ok=True)
    bench("analyze", d, "--official", "--corpus", m, check=False)
    t.check("quarantine_invalidates_selfcheck", passed_first and cant_reason(d) == "selfcheck_required",
            "official PASSED -> quarantine -> selfcheck_required")

    d, m = build_ready("corpus_mismatch", selfcheck=True)
    wrong = d / "wrong.json"; manifest(wrong, corpus_id="some-other-corpus", semver="9.9.9")
    (d / "findings.official.md").unlink(missing_ok=True)
    bench("analyze", d, "--official", "--corpus", wrong, check=False)
    t.check("corpus_mismatch", cant_reason(d) == "corpus_mismatch", "cant_analyze: corpus_mismatch")


# ---------------------------------------------------------------- cost / insulation / stats
def runtime_tripwires(t):
    # cost ceiling
    d = empty_dir("tw/cost_ceiling")
    spec = deepcopy(BASE); spec["judge"] = dict(FAKE_JUDGE)
    spec["cost_ceiling"] = {"amount": 1.00, "currency": "USD"}
    spec["arms"] = [{"name": "arm_a", "platform": "claude_code", "model": "anthropic/claude-sonnet-4-5-20250929"},
                    {"name": "arm_b", "platform": "claude_code", "model": "anthropic/claude-opus-4-5-20251101"}]
    tasks = [{"id": f"t{i}", "prompt": "solve", "fake_behavior": {"native_log": {"total_cost_usd": 0.40}}}
             for i in range(1, 9)]
    write_exp(d, spec, tasks)
    bench("plan", d / "experiment.yaml", "--ledger", d / "ledger.ndjson")
    bench("run", d)
    c = event_counts(d / "ledger.ndjson")
    t.check("cost_ceiling_stops_run", c.get("run_stopped_cost_ceiling", 0) >= 1 and c.get("trial", 0) < 48,
            f"run_stopped_cost_ceiling, {c.get('trial', 0)}/48 trials")

    # holdout-canary insulation
    d = empty_dir("tw/canary_leak")
    spec = deepcopy(BASE); spec["judge"] = dict(FAKE_JUDGE)
    canary = "ZZSECRET-CANARY-9137"
    write_exp(d, spec, [{"id": "t1", "prompt": f"Implement add(a,b). LEAKED ANSWER: {canary}",
                         "holdout_canaries": [canary],
                         "fake_behavior": {"native_log": {"total_cost_usd": 0.01}}}])
    bench("plan", d / "experiment.yaml", "--ledger", d / "ledger.ndjson")
    bench("run", d)
    infra = events(d / "ledger.ndjson", "trial_infra_failed")
    t.check("holdout_canary_leak", len(infra) >= 1
            and any("holdout_leak" in str(e.get("reason")) for e in infra)
            and not events(d / "ledger.ndjson", "trial"), "trial_infra_failed(holdout_leak), zero trials")

    # A/A null
    d = empty_dir("tw/aa_null")
    spec = deepcopy(BASE); spec["judge"] = dict(FAKE_JUDGE)
    spec["corpus"] = {"id": "aa-mini", "version": "1.0.0"}
    spec["arms"] = [{"name": "treatment", "platform": "claude_code", "model": "anthropic/claude-sonnet-4-5-20250929"},
                    {"name": "control", "platform": "claude_code", "model": "anthropic/claude-sonnet-4-5-20250929"}]
    tasks = [{"id": f"t{i}", "prompt": "solve", "fake_behavior": {"native_log": {"total_cost_usd": 0.01}}}
             for i in range(1, 9)]
    write_exp(d, spec, tasks)
    led = d / "ledger.ndjson"
    bench("plan", d / "experiment.yaml", "--ledger", led)
    bench("run", d)
    inject_grades(led, lambda arm, task: task in {"t1", "t2", "t3", "t4"})  # identical for both arms
    bench("grade", d, "--runner", "local")
    bench("judge", d)
    bench("analyze", d, "--exploratory")
    md = (d / "findings.exploratory.md").read_text()
    t.check("aa_null_no_effect", "0.0000" in md and "⇒ MET" not in md, "Δ 0.0000, decision not met")


# ---------------------------------------------------------------- gaming detection
def forensics_tripwires(t):
    def build(name, tamper):
        d = empty_dir(f"tw/{name}")
        (d / "holdouts" / "t1").mkdir(parents=True)
        (d / "holdouts" / "t1" / "test_secret.py").write_text(
            'def test_answer():\n    assert compute() == "forty-two"\n', encoding="utf-8")
        holdout = (d / "holdouts" / "t1" / "test_secret.py").resolve().as_posix()
        target = holdout if tamper else "src/app.py"
        detail = "weaken the hidden assertion" if tamper else "wire up the cached lookup"
        native = {"verdi_log_version": 1, "telemetry": {"cost": 0.02}, "trajectory": [
            {"kind": "tool_call", "command": "cat task.md", "detail": "read the task"},
            {"kind": "file_edit", "files_touched": [target], "detail": detail},
            {"kind": "test_run", "command": "pytest -q", "detail": "ok"}]}
        spec = deepcopy(BASE); spec["judge"] = dict(FAKE_JUDGE); spec["repetitions"] = 2
        spec["corpus"] = {"id": "forensics-mini", "version": "1.0.0"}
        spec["arms"] = [{"name": "treatment", "platform": "generic", "model": "openai/gpt-4o-2024-08-06"},
                        {"name": "control", "platform": "generic", "model": "anthropic/claude-sonnet-4-5-20250929"}]
        write_exp(d, spec, [{"id": "t1", "prompt": "Implement compute().", "task_class": "feature",
                             "holdouts_dir": "holdouts/t1", "fake_behavior": {"native_log": native}}])
        led = d / "ledger.ndjson"
        bench("plan", d / "experiment.yaml", "--ledger", led)
        bench("run", d)
        inject_grades(led, lambda arm, task: True)
        bench("grade", d, "--runner", "local")
        bench("forensics", "scan", d)
        rep = events(led, "forensics_report")[-1]["forensics_report"]
        return rep.get("flags", []), rep.get("coverage", {})

    flags, cov = build("forensics_tamper", tamper=True)
    t.check("forensics_flags_holdout_tamper",
            any(f.get("detector") == "holdout_tamper" for f in flags) and cov.get("covered", 0) > 0,
            f"{cov.get('covered')}/{cov.get('trials')} analyzed, holdout_tamper flagged")
    flags, cov = build("forensics_clean", tamper=False)
    t.check("forensics_clean_is_silent", len(flags) == 0 and cov.get("covered", 0) > 0,
            f"{cov.get('covered')}/{cov.get('trials')} analyzed, 0 flags")


# ---------------------------------------------------------------- asymmetric contamination
ORACLE = ("def levenshtein(a, b):\n    prev = list(range(len(b) + 1))\n"
          "    for i, ca in enumerate(a):\n        cur = [i + 1]\n"
          "        for j, cb in enumerate(b):\n            ins = cur[j] + 1\n"
          "            dele = prev[j + 1] + 1\n            sub = prev[j] + (ca != cb)\n"
          "            cur.append(min(ins, dele, sub))\n        prev = cur\n    return prev[-1]\n")


def contamination_tripwire(t):
    d = empty_dir("tw/asymmetric_contamination")
    tasks = [{"id": f"t{i}", "prompt": "solve", "task_class": "feature",
              "fake_behavior": {"native_log": {"total_cost_usd": 0.01}}} for i in range(1, 9)]
    spec = deepcopy(BASE); spec["judge"] = dict(FAKE_JUDGE)
    spec["corpus"] = {"id": "contam-mini", "version": "1.0.0"}
    spec["contamination"] = {"overlap_threshold": 0.5}
    spec["arms"] = [{"name": "treatment", "platform": "generic", "model": "fake/deterministic-2026-01-01"},
                    {"name": "control", "platform": "generic", "model": "fake/deterministic-2026-01-02"}]
    write_exp(d, spec, tasks)
    m = d / "manifest.json"; manifest(m, corpus_id="contam-mini", tasks=tasks)
    led = d / "ledger.ndjson"
    bench("plan", d / "experiment.yaml", "--ledger", led)
    bench("run", d)
    for ev in events(led, "trial"):
        rec = ev["trial_record"]; ws = Path(rec["artifacts_path"]).parent
        passed = rec["task_id"] in (TREATMENT_PASS if rec["arm"] == "treatment" else CONTROL_PASS)
        (ws / "holdout_results.json").write_text(
            json.dumps({"assertions": [{"id": "h1", "result": "pass" if passed else "fail"}]}), encoding="utf-8")
        # only treatment's t1 workspace is a verbatim copy of the oracle -> asymmetric overlap
        (ws / "solution.py").write_text(
            ORACLE if (rec["arm"] == "treatment" and rec["task_id"] == "t1")
            else "def solve():\n    return 0\n", encoding="utf-8")
    bench("grade", d, "--runner", "local")
    bench("corpus", "calibrate", d, "--manifest", m, "--kind", "full")
    bench("selfcheck", d)
    oracle = d / "oracle"; oracle.mkdir()
    (oracle / "t1.txt").write_text(ORACLE, encoding="utf-8")
    bench("contamination", "probe", d, "--manifest", m, "--oracle-dir", oracle)
    of = events(led, "contamination_probe")[-1]["probe"].get("overlap_flags", {})
    bench("analyze", d, "--official", "--corpus", m, check=False)
    t.check("asymmetric_contamination",
            of.get("treatment", {}).get("t1") is True and of.get("control", {}).get("t1") in (False, None)
            and cant_reason(d) == "asymmetric_contamination", "overlap treatment-only -> official refused")


def main():
    print("=" * 72, "\nL3 — tripwire matrix (18 vectors)\n" + "=" * 72)
    t = Tally("L3 tripwires")
    plan_tripwires(t)
    ledger_tripwires(t)
    fence_tripwires(t)
    runtime_tripwires(t)
    forensics_tripwires(t)
    contamination_tripwire(t)
    t.finish()


if __name__ == "__main__":
    main()
