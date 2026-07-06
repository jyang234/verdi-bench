"""L3 — the tripwire matrix: 18 adversarial vectors, every fence must fire (hermetic).

Grouped: pre-registration refusals, ledger tamper, analyze fence, cost/insulation/
stats, gaming detection, asymmetric contamination. No keys, no Docker (fake judge,
fake/arm models). Exits nonzero unless all 18 fire as designed.

Authored + driven through ``harness.sdk`` (refactor 02/08): the vectors are the
content; the plumbing is SDK builder mutations + workspace calls. The
pre-registration refusals stay on the installed ``bench plan`` console script
(the vector's *point* is the CLI refusal→exit-code mapping; ANSI is stripped in
``bench`` for the FORCE_COLOR flake); the byte-flip is a raw local tamper whose
detection is then in-process. Nothing here imports ``tests.*``.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, bench, dump_yaml, empty_dir  # noqa: E402

from harness.corpus.manifest import build_manifest  # noqa: E402
from harness.ledger.anchors import AnchorIntegrityError  # noqa: E402
from harness.schema import spec_to_yaml, tasks_to_yaml  # noqa: E402
from harness.sdk import Experiment, LedgerView, Task, write_holdout_results  # noqa: E402

FAKE_JUDGE = dict(escalation={"kappa_threshold": 0.6, "min_human_verdicts": 1})
TREATMENT_PASS = {"t1", "t2", "t3", "t4", "t5", "t6"}
CONTROL_PASS = {"t1", "t2"}


def fake_experiment(name="tw", *, seed=1234, ceiling=25.0) -> Experiment:
    """A base experiment on the fake judge — vectors add arms/tasks/config."""
    return Experiment(name, seed=seed, cost_ceiling_usd=ceiling).judge(
        "fake/deterministic-2026-01-01", **FAKE_JUDGE
    )


def golden_arms(exp: Experiment) -> Experiment:
    return (exp.arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
               .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code"))


def cost_tasks(exp: Experiment, cost: float, *, task_class=None) -> Experiment:
    for i in range(1, 9):
        exp.task(Task(f"t{i}", prompt="solve", task_class=task_class,
                      fake_behavior={"native_log": {"total_cost_usd": cost}}))
    return exp


def make_manifest(path, *, corpus_id="shakedown-mini", semver="1.0.0", task_ids=None) -> None:
    ids = task_ids or [f"t{i}" for i in range(1, 9)]
    build_manifest(corpus_id=corpus_id, semver=semver, kind="public",
                   tasks=[{"task_id": tid, "sha": hashlib.sha256(tid.encode()).hexdigest()}
                          for tid in ids]).save(path)


def cant_reason(ws):
    ev = ws.view().latest("cant_analyze")
    return ev.get("reason") if ev else None


# ---------------------------------------------------------------- pre-registration
def plan_tripwires(t):
    # One valid base, serialized through spec_to_yaml (single validation source),
    # then each vector mutates the dict to inject a p-hack the console script must
    # refuse. Driven through the installed `bench plan` (exit-code + reason).
    spec, tasks, rubric = cost_tasks(golden_arms(
        fake_experiment("tw").corpus("shakedown-mini", "1.0.0").repetitions(3)), 0.02).build()
    base, tasks_yaml = yaml.safe_load(spec_to_yaml(spec)), tasks_to_yaml(tasks)

    def run(name, mutate, expect):
        d = empty_dir(f"tw/{name}")
        s = deepcopy(base); mutate(s)
        dump_yaml(d / "experiment.yaml", s)
        (d / "tasks.yaml").write_text(tasks_yaml, encoding="utf-8")
        (d / "rubric.md").write_text(rubric, encoding="utf-8")
        r = bench("plan", d / "experiment.yaml", "--ledger", d / "ledger.ndjson", check=False)
        out = (r.stdout or "") + (r.stderr or "")
        led = d / "ledger.ndjson"
        locked = led.exists() and LedgerView(led).by_kind("experiment_locked")
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
    ws = cost_tasks(golden_arms(
        fake_experiment("tw").corpus("shakedown-mini", "1.0.0").repetitions(3)), 0.02).write(d)
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    clean_ok = ws.verify_chain().chain_ok
    # flip one byte of a NON-head line (raw local tamper), detection is in-process
    led = ws.ledger
    lines = led.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])
    obj["provenance"]["actor"] = obj["provenance"].get("actor", "x") + "_TAMPERED"
    lines[1] = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    led.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = ws.verify_chain()
    t.check("ledger_tamper_detected",
            clean_ok and not v.chain_ok and "broken link" in v.chain_detail.lower(),
            "clean OK then tamper -> CHAIN BROKEN")
    try:
        ws.anchor(out=d / "anchors.ndjson")
        anchored = True
    except AnchorIntegrityError:
        anchored = False
    t.check("anchor_refuses_tampered", not anchored and not (d / "anchors.ndjson").exists(),
            "refused; no anchor written")


# ---------------------------------------------------------------- analyze fence
def build_ready(name, selfcheck=True):
    d = empty_dir(f"tw/{name}")
    ws = cost_tasks(golden_arms(
        fake_experiment("tw").corpus("shakedown-mini", "1.0.0").repetitions(3)), 0.02).write(d)
    m = d / "manifest.json"; make_manifest(m)
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    ws.inject_holdout_results(
        lambda arm, task: task in (TREATMENT_PASS if arm == "treatment" else CONTROL_PASS))
    ws.grade(runner="local")
    ws.judge()
    ws.calibrate(manifest_path=m, kind="full")
    if selfcheck:
        ws.selfcheck()
    return ws, m


def fence_tripwires(t):
    ws, m = build_ready("official_before_selfcheck", selfcheck=False)
    ws.analyze(official_corpus=m)
    t.check("official_before_selfcheck", cant_reason(ws) == "selfcheck_required"
            and not (ws.dir / "findings.official.md").exists(), "cant_analyze: selfcheck_required")

    ws, m = build_ready("quarantine_stale", selfcheck=True)
    ws.analyze(official_corpus=m)  # baseline PASS
    passed_first = (ws.dir / "findings.official.md").exists()
    tid = ws.view().trials()[0].record["trial_id"]
    ws.quarantine(trial_id=tid, reason="shakedown: confirmed tamper")
    (ws.dir / "findings.official.md").unlink(missing_ok=True)
    ws.analyze(official_corpus=m)
    t.check("quarantine_invalidates_selfcheck", passed_first and cant_reason(ws) == "selfcheck_required",
            "official PASSED -> quarantine -> selfcheck_required")

    ws, m = build_ready("corpus_mismatch", selfcheck=True)
    wrong = ws.dir / "wrong.json"; make_manifest(wrong, corpus_id="some-other-corpus", semver="9.9.9")
    (ws.dir / "findings.official.md").unlink(missing_ok=True)
    ws.analyze(official_corpus=wrong)
    t.check("corpus_mismatch", cant_reason(ws) == "corpus_mismatch", "cant_analyze: corpus_mismatch")


# ---------------------------------------------------------------- cost / insulation / stats
def runtime_tripwires(t):
    # cost ceiling
    d = empty_dir("tw/cost_ceiling")
    exp = (fake_experiment("tw", ceiling=1.00).corpus("shakedown-mini", "1.0.0").repetitions(3)
           .arm("arm_a", model="anthropic/claude-sonnet-4-5-20250929", platform="claude_code")
           .arm("arm_b", model="anthropic/claude-opus-4-5-20251101", platform="claude_code"))
    ws = cost_tasks(exp, 0.40).write(d)
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    from collections import Counter
    c = Counter(e.get("event") for e in ws.view().events)
    t.check("cost_ceiling_stops_run", c.get("run_stopped_cost_ceiling", 0) >= 1 and c.get("trial", 0) < 48,
            f"run_stopped_cost_ceiling, {c.get('trial', 0)}/48 trials")

    # holdout-canary insulation
    d = empty_dir("tw/canary_leak")
    canary = "ZZSECRET-CANARY-9137"
    ws = golden_arms(fake_experiment("tw").corpus("shakedown-mini", "1.0.0").repetitions(3)).task(
        Task("t1", prompt=f"Implement add(a,b). LEAKED ANSWER: {canary}", holdout_canaries=(canary,),
             fake_behavior={"native_log": {"total_cost_usd": 0.01}})).write(d)
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    infra = ws.view().by_kind("trial_infra_failed")
    t.check("holdout_canary_leak", len(infra) >= 1 and any("holdout_leak" in str(e.get("reason")) for e in infra)
            and not ws.view().by_kind("trial"), "trial_infra_failed(holdout_leak), zero trials")

    # A/A null (identical arms, identical grades)
    d = empty_dir("tw/aa_null")
    aa = "anthropic/claude-sonnet-4-5-20250929"
    ws = cost_tasks((fake_experiment("tw").corpus("aa-mini", "1.0.0").repetitions(3)
                     .arm("treatment", model=aa, platform="claude_code")
                     .arm("control", model=aa, platform="claude_code")), 0.01).write(d)
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    ws.inject_holdout_results(lambda arm, task: task in {"t1", "t2", "t3", "t4"})  # identical
    ws.grade(runner="local")
    ws.judge()
    ws.analyze(exploratory=True)
    md = (ws.dir / "findings.exploratory.md").read_text()
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
        ws = (fake_experiment("tw").corpus("forensics-mini", "1.0.0").repetitions(2)
              .arm("treatment", model="openai/gpt-4o-2024-08-06", platform="generic")
              .arm("control", model="anthropic/claude-sonnet-4-5-20250929", platform="generic")
              .task(Task("t1", prompt="Implement compute().", task_class="feature",
                         holdouts_dir="holdouts/t1", fake_behavior={"native_log": native}))).write(d)
        ws.plan(actor="shakedown")
        ws.run(engine="fake")
        ws.inject_holdout_results(lambda arm, task: True)
        ws.grade(runner="local")
        ws.forensics()
        rep = ws.view().latest("forensics_report")["forensics_report"]
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
    ws = cost_tasks((fake_experiment("tw").corpus("contam-mini", "1.0.0").repetitions(3)
                     .contamination(overlap_threshold=0.5)
                     .arm("treatment", model="fake/deterministic-2026-01-01", platform="generic")
                     .arm("control", model="fake/deterministic-2026-01-02", platform="generic")),
                    0.01, task_class="feature").write(d)
    m = d / "manifest.json"; make_manifest(m, corpus_id="contam-mini")
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    for tv in ws.view().trials():
        rec = tv.record; wsdir = Path(rec["artifacts_path"]).parent
        write_holdout_results(
            wsdir, rec["task_id"] in (TREATMENT_PASS if rec["arm"] == "treatment" else CONTROL_PASS))
        # only treatment's t1 workspace is a verbatim copy of the oracle -> asymmetric overlap
        (wsdir / "solution.py").write_text(
            ORACLE if (rec["arm"] == "treatment" and rec["task_id"] == "t1")
            else "def solve():\n    return 0\n", encoding="utf-8")
    ws.grade(runner="local")
    ws.calibrate(manifest_path=m, kind="full")
    ws.selfcheck()
    oracle = d / "oracle"; oracle.mkdir()
    (oracle / "t1.txt").write_text(ORACLE, encoding="utf-8")
    probe = ws.contamination_probe(manifest_path=m, oracle_dir=oracle, actor="shakedown")
    of = probe.probe.get("overlap_flags", {})
    ws.analyze(official_corpus=m)
    t.check("asymmetric_contamination",
            of.get("treatment", {}).get("t1") is True and of.get("control", {}).get("t1") in (False, None)
            and cant_reason(ws) == "asymmetric_contamination", "overlap treatment-only -> official refused")


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
