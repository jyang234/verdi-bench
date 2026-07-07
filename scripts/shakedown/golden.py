"""L1 — golden path: the full pipeline on the fake engine + fake judge (hermetic).

Authored + driven in-process through ``harness.sdk`` (refactor 02): build -> write
-> plan -> run -> [inject per-arm grades] -> grade -> judge -> review (capture-then-
reveal) -> process -> analyze --exploratory -> forensics -> verify-chain, then assert
the known positive control (Delta +0.5, decision MET) is recovered. No keys, no Docker.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, banner, empty_dir  # noqa: E402
from _scenario import golden_experiment, golden_passes  # noqa: E402

from harness.process.rubric import default_rubric  # noqa: E402
from harness.review.record import RevealError  # noqa: E402


def main():
    banner("L1 — golden path (fake engine + fake judge)")
    # arm_a is treatment (paired delta = treatment - control), so the pre-registered
    # `delta_holdout_pass_rate > 0` reads "treatment improves over control".
    exp = golden_experiment("golden")

    ws = exp.write(empty_dir("golden"))
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    ws.inject_holdout_results(golden_passes)   # operator per-arm grades (arm-blind engine)
    ws.grade(runner="local")
    ws.judge()

    ws.review_build()
    built = ws.view().by_kind("review_packet_built")
    assert built, "review build produced no packets"
    try:                                        # reveal before a verdict must refuse
        ws.review_reveal(comparison_id=built[0]["comparison_id"])
        reveal_gated = False
    except RevealError:
        reveal_gated = True
    for b in built:                             # verdict every comparison, then reveal the batch
        ws.review_record(comparison_id=b["comparison_id"], winner="1",
                         arm_recognized=True, arm_guess=b["response_map"]["1"])
    for b in built:
        ws.review_reveal(comparison_id=b["comparison_id"])

    ws.process_score()
    control_trial = next(t.record["trial_id"] for t in ws.view().trials()
                         if t.record["arm"] == "control")
    rubric = default_rubric()
    ws.process_record(trial_id=control_trial, comparison_id=built[0]["comparison_id"],
                      scores={dim: 4 for dim in rubric.dimension_ids}, rubric=rubric)

    ws.analyze(exploratory=True)
    ws.forensics()
    chain = ws.verify_chain()

    md = (ws.dir / "findings.exploratory.md").read_text(encoding="utf-8")
    counts = Counter(e.get("event") for e in ws.view().events)
    t = Tally("L1 golden")
    t.check("pipeline one-event-per-op", counts["experiment_locked"] == 1 and counts["findings_rendered"] == 1,
            f"counts={dict(sorted(counts.items()))}")
    t.check("capture-then-reveal gate", reveal_gated, "reveal-before-verdict refused")
    t.check("positive control recovered", "mean paired delta: 0.5000" in md and "⇒ MET" in md,
            "Δ +0.5000, decision MET" if "⇒ MET" in md else "decision NOT met")
    t.check("chain verifies", chain.chain_ok, "chain OK")
    t.finish()


if __name__ == "__main__":
    main()
