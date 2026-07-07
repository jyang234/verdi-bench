"""The master-plan §3 north-star UX, fake-engine variant [refactor 02 §8].

The plan's headline claim is that a newcomer goes from an empty directory to a
graded, judged, analyzed A/B in ~15 lines of Python. This drives the exact
snippet — builder -> write -> plan -> run -> grade -> judge -> analyze ->
verify_chain — on the fake engine (no MeteringProxy / official_image; those are
Phase 3), with the fake path's ``write_holdout_results`` operator step standing
in for real holdout execution. It is the executable proof that the SDK composes
the tested subsystems into one working write path.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from harness.sdk import Experiment, Task, write_holdout_results


def test_northstar_fake_engine_ab(tmp_path):
    exp = (
        Experiment("mini-ab", seed=1234, cost_ceiling_usd=10.0)
        .arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
        .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
        .judge("fake/deterministic-2026-01-01")  # rubric defaults to the library template
        .task(Task("t_add", prompt="Write solution.py defining add(a, b)...",
                   fake_behavior={"native_log": {"total_cost_usd": 0.01}}))
        .task(Task("t_pal", prompt="Write solution.py defining is_palindrome(s)...",
                   fake_behavior={"native_log": {"total_cost_usd": 0.01}}))
    )

    ws = exp.write(tmp_path / "mini-ab")
    # write path is pre-lock and complete
    assert {p.name for p in ws.dir.iterdir()} >= {
        "experiment.yaml", "tasks.yaml", "rubric.md"
    }

    ws.plan(actor="shakedown")
    ws.run(engine="fake")

    # fake-path operator injection (the arm-blind engine needs the asymmetry
    # written between run and grade): treatment passes both tasks, control neither.
    for tv in ws.view().trials():
        rec = tv.record
        write_holdout_results(Path(rec["artifacts_path"]).parent, rec["arm"] == "treatment")

    ws.grade(runner="local")
    ws.judge()
    findings = ws.analyze(exploratory=True)
    verdict = ws.verify_chain()

    assert findings is not None and findings.name == "findings.exploratory.md"
    assert verdict.chain_ok
    md = findings.read_text(encoding="utf-8")
    # the known effect is recovered exactly: arm_a is the first-added arm
    # (treatment), which beats control on both tasks, so the contender-frame
    # paired delta is +1.0000 — a deterministic, re-derivable point estimate.
    assert "mean paired delta: 1.0000" in md

    counts = Counter(e.get("event") for e in ws.view().events)
    # one event per operation: exactly one lock, one findings render; 2 tasks x
    # 2 arms x 1 rep = 4 trials.
    assert counts["experiment_locked"] == 1
    assert counts["findings_rendered"] == 1
    assert counts["trial"] == 4
