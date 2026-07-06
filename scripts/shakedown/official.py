"""L2 — passing official render with a REAL LLM judge (opt-in: needs ANTHROPIC_API_KEY).

Walks the full pre-registration fence to a PASSING `analyze --official`, using a
real Anthropic judge (identity-blind, both orders). Emits the dossier + result
card + static operator bundle. Overrides the committed golden spec's fake judge.

  $ uv run --env-file .env python scripts/shakedown/official.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ASSETS, Tally, bench, dump_yaml, events, inject_grades, load_yaml, stage  # noqa: E402

JUDGE_MODEL = "anthropic/claude-haiku-4-5-20251001"  # date-versioned (non-alias); see memory
TREATMENT_PASS = {"t1", "t2", "t3", "t4", "t5", "t6"}
CONTROL_PASS = {"t1", "t2"}


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set — L2 uses a real judge. "
                         "Run: uv run --env-file .env python scripts/shakedown/official.py")
    print("=" * 72, "\nL2 — passing official render + REAL Anthropic judge\n" + "=" * 72)
    d = stage("official")
    led = d / "ledger.ndjson"
    spec = load_yaml(d / "experiment.yaml")
    spec["judge"]["model"] = JUDGE_MODEL          # real judge overrides the committed fake judge
    dump_yaml(d / "experiment.yaml", spec)
    tasks = load_yaml(ASSETS / "golden" / "tasks.yaml")["tasks"]
    m = d / "manifest.json"
    m.write_text(json.dumps({
        "corpus_id": "shakedown-mini", "semver": "1.0.0", "kind": "public",
        "tasks": [{"task_id": t["id"], "sha": hashlib.sha256(t["id"].encode()).hexdigest(),
                   "status": "admitted", "metadata": {"category": t.get("task_class", "misc")}}
                  for t in tasks]}, indent=2), encoding="utf-8")

    bench("plan", d / "experiment.yaml", "--ledger", led)
    bench("run", d)
    inject_grades(led, lambda arm, task: task in (TREATMENT_PASS if arm == "treatment" else CONTROL_PASS))
    bench("grade", d, "--runner", "local")
    bench("judge", d)                              # REAL Anthropic calls
    bench("corpus", "calibrate", d, "--manifest", m, "--kind", "full")
    bench("selfcheck", d)
    off = bench("analyze", d, "--official", "--corpus", m, check=False)
    bench("analyze", d, "--exploratory")
    bench("card", "emit", d, "--corpus", m, "--format", "json", "--out", d / "card.json")
    bench("serve", d, "--bundle", d / "operator.bundle.html")
    bench("verify-chain", led)

    sc = events(led, "selfcheck")[-1]
    jv = [e["verdict"] for e in events(led, "judge_verdict")]
    from collections import Counter
    winners = Counter(v.get("winner") for v in jv)
    order_incon = sum(1 for v in jv if v.get("order_inconsistent"))
    md = (d / "findings.official.md").read_text() if (d / "findings.official.md").exists() else ""
    t = Tally("L2 official + real judge")
    t.check("official fence PASSED", off.returncode == 0 and (d / "findings.official.md").exists(), "exit 0, rendered")
    t.check("no exploratory watermark", "EXPLORATORY" not in md, "official render is unwatermarked")
    t.check("selfcheck passed", sc.get("passed") is True, f"coverage={sc.get('coverage')}")
    t.check("real judge, order-consistent", len(jv) > 0 and order_incon == 0
            and JUDGE_MODEL in {v.get("provenance", {}).get("judge_model") for v in jv},
            f"{len(jv)} verdicts winners={dict(winners)}")
    t.check("dossier + card + bundle emitted",
            (d / "findings.exploratory.dossier.html").exists() and (d / "card.json").exists()
            and (d / "operator.bundle.html").exists(), "3 artifacts written")
    t.finish()


if __name__ == "__main__":
    main()
