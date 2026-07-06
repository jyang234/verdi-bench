"""L2 — passing official render with a REAL LLM judge (opt-in: needs ANTHROPIC_API_KEY).

Authored + driven in-process through ``harness.sdk`` (refactor 02): walks the full
pre-registration fence to a PASSING official render using a real Anthropic judge
(identity-blind, both orders), then emits the dossier + result card + static
operator bundle.

  $ uv run --env-file .env python scripts/shakedown/official.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, empty_dir  # noqa: E402

from harness.corpus.manifest import build_manifest  # noqa: E402
from harness.sdk import Experiment, MissingEnvKeysError, Task, require_env_keys  # noqa: E402
from harness.serve.bundle import write_bundle  # noqa: E402

JUDGE_MODEL = "anthropic/claude-haiku-4-5-20251001"  # date-versioned (non-alias); see memory
TREATMENT_PASS = {"t1", "t2", "t3", "t4", "t5", "t6"}
CONTROL_PASS = {"t1", "t2"}


def main():
    try:
        require_env_keys("ANTHROPIC_API_KEY")           # L2 uses a real judge
    except MissingEnvKeysError as e:
        raise SystemExit(f"{e}\nrun: uv run --env-file .env python scripts/shakedown/official.py")
    print("=" * 72, "\nL2 — passing official render + REAL Anthropic judge\n" + "=" * 72)
    exp = (Experiment("official", seed=1234, cost_ceiling_usd=25.0)
           .arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
           .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
           .judge(JUDGE_MODEL, escalation={"kappa_threshold": 0.6, "min_human_verdicts": 1})
           .corpus("shakedown-mini", "1.0.0").repetitions(3))
    for i in range(1, 9):
        exp.task(Task(f"t{i}", prompt="solve",
                      fake_behavior={"native_log": {"total_cost_usd": 0.02}}))
    ws = exp.write(empty_dir("official"))
    m = ws.dir / "manifest.json"
    build_manifest(corpus_id="shakedown-mini", semver="1.0.0", kind="public",
                   tasks=[{"task_id": f"t{i}", "sha": hashlib.sha256(f"t{i}".encode()).hexdigest()}
                          for i in range(1, 9)]).save(m)

    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    ws.inject_holdout_results(
        lambda arm, task: task in (TREATMENT_PASS if arm == "treatment" else CONTROL_PASS))
    ws.grade(runner="local")
    ws.judge()                                          # REAL Anthropic calls
    ws.calibrate(manifest_path=m, kind="full")
    sc = ws.selfcheck()
    off = ws.analyze(official_corpus=m)                 # fenced official render
    ws.analyze(exploratory=True)
    ws.card(corpus=m, fmt="json", out=ws.dir / "card.json")
    write_bundle(ws.dir, ws.dir / "operator.bundle.html")
    chain = ws.verify_chain()

    jv = [e["verdict"] for e in ws.view().by_kind("judge_verdict")]
    order_incon = sum(1 for v in jv if v.get("order_inconsistent"))
    off_md = ws.dir / "findings.official.md"
    md = off_md.read_text() if off_md.exists() else ""
    t = Tally("L2 official + real judge")
    t.check("official fence PASSED", off is not None and off_md.exists(), "rendered")
    t.check("no exploratory watermark", "EXPLORATORY" not in md, "official render is unwatermarked")
    t.check("selfcheck passed", sc.get("passed") is True, f"coverage={sc.get('coverage')}")
    t.check("real judge, order-consistent",
            len(jv) > 0 and order_incon == 0
            and JUDGE_MODEL in {v.get("provenance", {}).get("judge_model") for v in jv},
            f"{len(jv)} verdicts, order_inconsistent={order_incon}")
    t.check("dossier + card + bundle emitted",
            (ws.dir / "findings.exploratory.dossier.html").exists() and (ws.dir / "card.json").exists()
            and (ws.dir / "operator.bundle.html").exists() and chain.chain_ok, "3 artifacts written")
    t.finish()


if __name__ == "__main__":
    main()
