"""L1 — golden path: the full pipeline on the fake engine + fake judge (hermetic).

plan -> run -> [inject per-arm grades] -> grade -> judge -> review (capture-then-
reveal) -> process -> analyze --exploratory -> forensics -> verify-chain, then
assert the known positive control is recovered. No keys, no Docker.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, bench, event_counts, events, inject_grades, stage  # noqa: E402

TREATMENT_PASS = {"t1", "t2", "t3", "t4", "t5", "t6"}
CONTROL_PASS = {"t1", "t2"}


def passes(arm, task):
    return task in (TREATMENT_PASS if arm == "treatment" else CONTROL_PASS)


def main():
    print("=" * 72, "\nL1 — golden path (fake engine + fake judge)\n" + "=" * 72)
    d = stage("golden")
    led = d / "ledger.ndjson"

    bench("plan", d / "experiment.yaml", "--ledger", led)
    bench("run", d)
    inject_grades(led, passes)
    bench("grade", d, "--runner", "local")
    bench("judge", d)

    bench("review", "build", d)
    built = events(led, "review_packet_built")
    assert built, "review build produced no packets"
    # reveal before a verdict must refuse (capture-then-reveal)
    pre = bench("review", "reveal", d, "--comparison-id", built[0]["comparison_id"], check=False)
    reveal_gated = pre.returncode != 0
    # a reveal unblinds the whole batch -> verdict every comparison first
    for b in built:
        bench("review", "record", d, "--comparison-id", b["comparison_id"],
              "--winner", "1", "--arm-recognized", "--arm-guess", b["response_map"]["1"])
    for b in built:
        bench("review", "reveal", d, "--comparison-id", b["comparison_id"])

    bench("process", "score", d)
    control_trial = next(e["trial_record"]["trial_id"] for e in events(led, "trial")
                         if e["trial_record"]["arm"] == "control")
    from harness.process.rubric import default_rubric
    scores = d / "human_scores.json"
    scores.write_text(json.dumps({dim: 4 for dim in default_rubric().dimension_ids}), encoding="utf-8")
    bench("process", "record", d, "--trial-id", control_trial,
          "--comparison-id", built[0]["comparison_id"], "--scores", scores)

    bench("analyze", d, "--exploratory")
    bench("forensics", "scan", d)
    chain = bench("verify-chain", led, check=False)

    md = (d / "findings.exploratory.md").read_text(encoding="utf-8")
    counts = event_counts(led)
    t = Tally("L1 golden")
    t.check("pipeline one-event-per-op", counts.get("experiment_locked") == 1 and counts.get("findings_rendered") == 1,
            f"counts={json.dumps(counts)}")
    t.check("capture-then-reveal gate", reveal_gated, "reveal-before-verdict refused")
    t.check("positive control recovered", "mean paired delta: 0.5000" in md and "⇒ MET" in md,
            "Δ +0.5000, decision MET" if "⇒ MET" in md else "decision NOT met")
    t.check("chain verifies", chain.returncode == 0, "chain OK")
    t.finish()


if __name__ == "__main__":
    main()
