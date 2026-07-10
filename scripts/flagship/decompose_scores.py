#!/usr/bin/env python3
"""Decompose the fused groundwork holdout score, retrospectively and offline.

[design: docs/design/mechanism-decomposition-program.md, piece 0]

Every groundwork-v0 holdout is ONE fused command — feature tests AND the
structural gate (`set -e; …; go test ./...; verdi-groundwork-check <task>`) —
so `binary_score` conflates two channels (independent review §3.1). This script
re-executes the two halves SEPARATELY against each graded trial's preserved
workspace, inside the pinned grader image, and emits a decomposed table:
functional-pass / gate-pass / fused per task x arm x experiment.

Ground rules:
  * NO ledger mutation — the chains are immutable; output is an analysis
    artifact (DECOMPOSITION.json + DECOMPOSITION.md beside the experiments).
  * Self-validating: recomputed fused (functional AND gate) must equal the
    recorded `binary_score` for every trial; any mismatch is listed and the
    exit code is nonzero. A silent divergence would be a wrong instrument.
  * Workspaces are graded on a throwaway copy (mirroring DockerGradeRunner's
    fail-safe posture), network-less, holdouts mounted read-only.
  * The gate half is re-executed WITHOUT the functional side-file injections the
    fused command performs first, but under the corpus's RTA substrate those test
    files do not enter the flowmap graph, so the gate verdict is unaffected — and
    any divergence that would flip the fused AND is caught by the fused-vs-recorded
    mismatch fence.

Usage:
    VERDI_GRADER_IMAGE=<digest> uv run python scripts/flagship/decompose_scores.py \
        runs/consistency/recon2 runs/consistency/instructed ... \
        [--out runs/consistency]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GRADER_IMAGE_ENV = "VERDI_GRADER_IMAGE"
DOCKER_TIMEOUT_S = 600


def split_holdout_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split the fused corpus holdout into (functional, gate) argvs.

    The builder's shape (build_tasks.holdout_argv) is
    ``["sh","-c","set -e; H=…; <cps>; go test ./...; <wrapper> <task>"]``:
    the gate call is the final ``"; "``-separated segment and the functional
    half is everything before it. Any other shape is refused loudly — a
    guessed split would silently mis-score."""
    if len(argv) != 3 or argv[:2] != ["sh", "-c"]:
        raise ValueError(f"unexpected holdout argv shape: {argv!r}")
    script = argv[2]
    head, sep, gate = script.rpartition("; ")
    if not sep or not head.endswith("go test ./..."):
        raise ValueError(f"unexpected fused holdout script: {script!r}")
    return ["sh", "-c", head], ["sh", "-c", "set -e; " + gate]


def load_graded_trials(ledger_path) -> list[dict]:
    """Join trial and grade events: one row per GRADED trial.

    Ungraded trials are excluded (never guessed at). The advisory gate verdict
    (``id == "groundwork:verdict"``, source ``plugin:groundwork``) rides along
    for the cross-check column; absent stays ``None``."""
    trials: dict[str, dict] = {}
    grades: dict[str, dict] = {}
    for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        kind = ev.get("event")
        if kind in ("reused_trial", "reused_grade"):
            raise ValueError(
                f"ledger carries a {kind!r} event: this script does not decompose "
                "reused trials — a control-reuse ledger would have those cells "
                "silently absent from the decomposition. Decompose the source "
                "experiment's own ledger instead."
            )
        if ev.get("event") == "trial":
            tr = ev["trial_record"]
            trials[tr["trial_id"]] = {
                "trial_id": tr["trial_id"],
                "task_id": tr["task_id"],
                "arm": tr["arm"],
                "workspace": str(Path(tr["artifacts_path"]).parent),
            }
        elif ev.get("event") == "grade":
            grades[ev["trial_id"]] = ev
    rows: list[dict] = []
    for tid, t in sorted(trials.items()):
        g = grades.get(tid)
        if g is None:
            continue
        advisory = next(
            (a for a in g.get("assertions", []) if a.get("id") == "groundwork:verdict"),
            None,
        )
        rows.append({
            **t,
            "binary_score": g["binary_score"],
            "advisory_verdict": advisory.get("detail") if advisory else None,
        })
    return rows


def run_half_in_grader(image: str, workspace: str, holdouts_dir: Path,
                       argv: list[str]) -> tuple[bool, str]:
    """Run one holdout half in the grader image against a THROWAWAY workspace
    copy (never the ledgered original), network-less, holdouts read-only."""
    with tempfile.TemporaryDirectory() as td:
        ws_copy = Path(td) / "workspace"
        shutil.copytree(workspace, ws_copy, symlinks=True)
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network=none",
             "-v", f"{ws_copy}:/workspace",
             "-v", f"{holdouts_dir.resolve()}:/holdouts:ro",
             "-w", "/workspace", image, *argv],
            capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S,
        )
    return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()[-400:]


def decompose_experiment(exp_dir: Path, image: str) -> list[dict]:
    ledger = exp_dir / "ledger.ndjson"
    rows = []
    for t in load_graded_trials(ledger):
        holdouts_dir = exp_dir / "holdouts" / t["task_id"]
        declared = json.loads((holdouts_dir / "holdout.json").read_text(encoding="utf-8"))
        functional_argv, gate_argv = split_holdout_argv(declared["argv"])
        f_ok, f_detail = run_half_in_grader(image, t["workspace"], holdouts_dir,
                                            functional_argv)
        g_ok, g_detail = run_half_in_grader(image, t["workspace"], holdouts_dir,
                                            gate_argv)
        rows.append({
            **t,
            "experiment": exp_dir.name,
            "functional_pass": f_ok,
            "gate_pass": g_ok,
            "fused_recomputed": f_ok and g_ok,
            "fused_matches_recorded": (f_ok and g_ok) == t["binary_score"],
            "functional_detail": None if f_ok else f_detail,
            "gate_detail": None if g_ok else g_detail,
        })
        print(f"  {exp_dir.name} {t['trial_id']} {t['task_id']} {t['arm']}: "
              f"functional={'PASS' if f_ok else 'fail'} gate={'PASS' if g_ok else 'fail'}"
              + ("" if rows[-1]["fused_matches_recorded"] else "  ** MISMATCH **"))
    return rows


def render_markdown(rows: list[dict]) -> str:
    """Per experiment x task x arm: functional / gate / fused pass counts."""
    cells: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        key = (r["experiment"], r["task_id"], r["arm"])
        c = cells.setdefault(key, {"n": 0, "functional": 0, "gate": 0, "fused": 0})
        c["n"] += 1
        c["functional"] += r["functional_pass"]
        c["gate"] += r["gate_pass"]
        c["fused"] += r["fused_recomputed"]
    lines = [
        "# Decomposed scores (functional vs gate) — retrospective regrade",
        "",
        "> Generated by `scripts/flagship/decompose_scores.py`; no ledger was",
        "> mutated. `fused = functional AND gate` reproduces the recorded",
        "> `binary_score` on every row unless a MISMATCH is flagged below.",
        "",
        "| experiment | task | arm | n | functional | gate | fused |",
        "|---|---|---|--:|--:|--:|--:|",
    ]
    for (exp, task, arm), c in sorted(cells.items()):
        lines.append(f"| {exp} | {task} | {arm} | {c['n']} | "
                     f"{c['functional']}/{c['n']} | {c['gate']}/{c['n']} | "
                     f"{c['fused']}/{c['n']} |")
    mismatches = [r for r in rows if not r["fused_matches_recorded"]]
    lines += ["", f"Trials recomputed: {len(rows)}; "
              f"fused-vs-recorded mismatches: {len(mismatches)}"]
    for r in mismatches:
        lines.append(f"- MISMATCH {r['experiment']}/{r['trial_id']} "
                     f"({r['task_id']}, {r['arm']}): recomputed "
                     f"{r['fused_recomputed']} vs recorded {r['binary_score']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("experiments", nargs="+", type=Path,
                    help="experiment dirs (each with ledger.ndjson + holdouts/)")
    ap.add_argument("--out", type=Path, default=Path("runs/consistency"),
                    help="directory for DECOMPOSITION.json / DECOMPOSITION.md")
    args = ap.parse_args()
    image = os.environ.get(GRADER_IMAGE_ENV)
    if not image:
        print(f"REFUSED: set {GRADER_IMAGE_ENV} to the pinned grader image "
              "(the digest the program graded with)", file=sys.stderr)
        return 2
    rows: list[dict] = []
    for exp in args.experiments:
        rows.extend(decompose_experiment(exp, image))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "DECOMPOSITION.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / "DECOMPOSITION.md").write_text(render_markdown(rows), encoding="utf-8")
    mismatches = sum(not r["fused_matches_recorded"] for r in rows)
    print(f"\nwrote {args.out}/DECOMPOSITION.md ({len(rows)} trials, "
          f"{mismatches} mismatches)")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
