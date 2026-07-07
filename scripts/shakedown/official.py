"""L2 — passing official render with a REAL LLM judge (opt-in: needs ANTHROPIC_API_KEY).

Authored + driven in-process through ``harness.sdk`` (refactor 02): walks the full
pre-registration fence to a PASSING official render using a real Anthropic judge
(identity-blind, both orders), then emits the dossier + result card + static
operator bundle.

  $ uv run --env-file .env python scripts/shakedown/official.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, banner, empty_dir, require_keys_or_exit  # noqa: E402
from _scenario import advance_to_judged, golden_experiment, make_manifest  # noqa: E402

from harness.serve.bundle import write_bundle  # noqa: E402

JUDGE_MODEL = "anthropic/claude-haiku-4-5-20251001"  # date-versioned (non-alias); see memory


def main():
    require_keys_or_exit("ANTHROPIC_API_KEY", script="official.py")  # L2 uses a real judge
    banner("L2 — passing official render + REAL Anthropic judge")
    exp = golden_experiment("official", judge=JUDGE_MODEL)
    ws = exp.write(empty_dir("official"))
    m = ws.dir / "manifest.json"
    make_manifest(m)

    advance_to_judged(ws)                               # REAL Anthropic calls in ws.judge()
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
