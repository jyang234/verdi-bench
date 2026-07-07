# ============================================================================
# UNTESTED ON GPU -- workspace-pilot kit. Exercise with --smoke first.
# This generator is stdlib-only and runs here; it produces the tiny artifact
# fixtures the --offline-selftest paths assert against. Regenerate with:
#   python3 scripts/workspace-pilot/sample_data/make_samples.py
# ============================================================================
"""Reproducibly build the committed sample artifact fixtures.

The fixtures are hand-built (a few positions, k=3) but assembled through the SAME
capture_workspace builders that real captures use, so they exercise the true code
path and validate against the draft schema. Their probe log-probs are chosen so the
A.21 score has a KNOWN separation the selftests assert:

    sample-eval    : readouts -2.0, control -9.0  -> score  7.0  (eval-framed, high)
    sample-deploy  : readouts -9.0, control -8.5  -> score -0.5  (deployment, low)
    separation                                    ->        7.5

Frozen with mapping_version wt/v1-draft. (workspace-observability-plan.md C2/C3)
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PILOT = HERE.parent
sys.path.insert(0, str(PILOT))

from _wtcommon import (  # noqa: E402
    A21_READOUT_WORDS,
    MAPPING_VERSION,
    sha256_text,
    write_canonical,
)
import capture_workspace as cap  # noqa: E402

# Tiny, independent control set for the fixtures (4 nouns), kept separate from the
# frozen 100-noun production set so the fixtures stay tiny. control_logprobs align
# to this order.
SAMPLE_CONTROL = ["apple", "book", "chair", "cup"]
SAMPLE_CONTROL_TEXT = (
    "# ============================================================================\n"
    "# UNTESTED ON GPU -- workspace-pilot kit. TINY sample control set (4 nouns) for\n"
    "# --offline-selftest fixtures ONLY. The frozen production set is data/control_nouns.txt.\n"
    "# The loader skips '#' comments and blanks; order is load-bearing.\n"
    "# ============================================================================\n"
    + "\n".join(SAMPLE_CONTROL) + "\n"
)

NUM_MODEL_LAYERS = 8
BAND = "explicit:4,5"
TOP_K = 3


def _topk(a: str, b: str, c: str) -> list[tuple[str, float]]:
    # three readouts with distinct strengths (deterministic order after sort)
    return [(a, -0.5), (b, -1.5), (c, -2.5)]


def _raw_positions(kind: str) -> list[dict]:
    """Three positions; the last is the scored assistant-turn-start position."""
    strong = ("fictional", "simulation", "benchmark") if kind == "eval" else ("account", "user", "wallet")
    return [
        {"index": 0, "role": "prompt", "token": "You",
         "layers": [{"layer": 4, "topk": _topk("task", "request", "please")},
                    {"layer": 5, "topk": _topk("task", "help", "code")}]},
        {"index": 1, "role": "prompt", "token": "add",
         "layers": [{"layer": 4, "topk": _topk("endpoint", "handler", "route")},
                    {"layer": 5, "topk": _topk("service", "method", "func")}]},
        {"index": 2, "role": "assistant_turn_start", "token": "I",
         "layers": [{"layer": 4, "topk": _topk(*strong)},
                    {"layer": 5, "topk": _topk(*strong)}]},
    ]


def _raw_probe(kind: str) -> list[dict]:
    if kind == "eval":
        readout_lp = {"fictional": -2.0, "simulation": -2.0}
        control_lp = [-9.0, -9.0, -9.0, -9.0]
    else:
        readout_lp = {"fictional": -9.0, "simulation": -9.0}
        control_lp = [-8.5, -8.5, -8.5, -8.5]
    return [{"index": 2, "role": "assistant_turn_start",
             "layers": [{"layer": 4, "readout_logprobs": dict(readout_lp), "control_logprobs": list(control_lp)},
                        {"layer": 5, "readout_logprobs": dict(readout_lp), "control_logprobs": list(control_lp)}]}]


def build_sample(kind: str, prompt_class: str, control_sha: str) -> dict:
    pid = f"sample-{kind}"
    band = cap.parse_layer_band(BAND, NUM_MODEL_LAYERS)
    kept, truncation = cap.apply_position_cap(_raw_positions(kind), position_cap=1024)
    positions = cap.build_positions(kept, TOP_K)
    probe = cap.build_probe_readouts(_raw_probe(kind), control_sha, len(SAMPLE_CONTROL),
                                     A21_READOUT_WORDS, dropped_multitoken=[])
    pin = {"model_revision": "Qwen/Qwen3.5-4B@SAMPLE-not-a-real-revision",
           "lens_sha256": "SAMPLE-lens-sha-not-real", "mapping_version": MAPPING_VERSION}
    prompt_set = {"manifest_sha256": sha256_text(f"sample-manifest::{pid}"),
                  "prompt_id": pid, "prompt_class": prompt_class,
                  "prompt_sha256": sha256_text(f"sample-prompt::{pid}")}
    return cap.assemble_artifact(pin=pin, layer_band=band, top_k=TOP_K, prompt_set=prompt_set,
                                 positions=positions, probe_readouts=probe, truncation=truncation)


def main() -> int:
    print(cap.BANNER)
    control_path = HERE / "sample_control_nouns.txt"
    control_path.write_text(SAMPLE_CONTROL_TEXT, encoding="utf-8")
    control_sha = sha256_text(SAMPLE_CONTROL_TEXT)

    for kind, cls, fname in (("eval", "realism-eval", "sample_eval_trajectory.json"),
                             ("deploy", "deployment-control", "sample_deploy_trajectory.json")):
        art = build_sample(kind, cls, control_sha)
        ok, errs, method = cap.validate_artifact(art)
        if not ok:
            raise SystemExit(f"generated {fname} failed {method} validation: {errs}")
        sha = write_canonical(HERE / fname, art)
        print(f"  wrote {fname} (sha256 {sha[:12]}..; validated via {method})")
    print(f"  wrote sample_control_nouns.txt (sha256 {control_sha[:12]}..)")
    print("[make_samples] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
