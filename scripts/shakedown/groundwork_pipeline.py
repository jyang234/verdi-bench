#!/usr/bin/env python3
"""Keyless end-to-end pipeline smoke over corpus groundwork-v0 (plan §6 / §10 P3, local half).

A deterministic, keyless, no-Docker drive of the FULL verdi-bench pipeline over
the groundwork-v0 corpus on the FAKE engine (not fully hermetic — it needs the Go
toolchain + the verdi-go source to build/run the real gate), grading through the
real ``flowmap`` / ``groundwork`` binaries via the local-exec ADVISORY tier —
the A-P2 recipe applied per trial. It proves the groundwork gate discriminates a
clean solution from an invariant-violating one *through the whole pipeline*
(plan→run→grade→judge→selfcheck→analyze→verify-chain), which is the P3 pilot's
local exit criterion. The harbor half (real models solving in real containers,
metered egress) is the OTHER half of P3 and needs keys+proxy+Docker.

Convention: this follows ``scripts/shakedown`` (``golden.py`` / ``tripwires.py``)
— a runnable script (NOT a pytest), authored + driven through ``harness.sdk``,
staging under ``_run/``. The REUSABLE pieces (corpus materialization, corpus
authoring, per-arm materialization, grading, the discrimination table, the funnel
driver) live in ``_groundwork_lib`` so the flagship kit (``scripts/flagship/``)
shares them rather than re-deriving; this file keeps only the P3-smoke narrative.

    python scripts/shakedown/groundwork_pipeline.py [--plant-funnel-fixtures]

Binaries: ``VERDI_FLOWMAP_BIN`` / ``VERDI_GROUNDWORK_BIN`` if set (the corpus's
pinned toolchain), else built from the ``/home/user/verdi-go`` tip and the build
identity echoed. ``images/grader`` is put on PATH so ``verdi-groundwork-check``
resolves by bare name (the corpus's portable command holdout).

fake_behavior + per-arm asymmetry — the JUDGMENT CALL, stated plainly. The fake
engine is arm-BLIND: it reads only ``task.fake_behavior``, so "treatment gets the
solution tree, control gets the exemplar-violation tree" CANNOT be expressed
through ``fake_behavior`` natively. The faithful, sanctioned construction is to
materialize the arm's workspace tree BETWEEN run and grade
(``_groundwork_lib.materialize_arms``): grounded → the reference ``solution/``
tree, bare → the ``exemplar-violation/`` tree. Grading then regenerates the branch
graph from that tree and runs the real gate, so the PASS/FAIL is the gate's.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _groundwork_lib as gw  # noqa: E402
from _harness import Tally, banner, empty_dir  # noqa: E402

# §6 arm shape, seed, ceiling — the shakedown's known-answer scenario. The model
# is Opus (the smoke authors the headline tier); grounded/bare payload asymmetry
# and the never-invoked fake judge come from _groundwork_lib.
SEED, CEILING = 1234, 100.0


def author_experiment(expt: Path):
    """Author the §6 2-arm spec (grounded vs bare, same Opus model) over the emitted
    corpus tasks and write experiment.yaml + tasks.yaml + rubric into ``expt`` (the
    builder's ``holdouts/`` stays). The SDK ``TaskSpec`` validates each task in."""
    exp = gw.build_two_arm("groundwork-v0-p3-smoke", model=gw.MODEL_OPUS, seed=SEED,
                           ceiling=CEILING, judge=gw.PLACEHOLDER_JUDGE, reps=1)
    gw.add_corpus_tasks(exp, gw.load_corpus_tasks(expt))
    return exp.write(expt)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plant-funnel-fixtures", action="store_true",
                    help="plant synthetic MCP/trajectory fixtures into 2 grounded trials and run the funnel tool")
    args = ap.parse_args()

    banner("groundwork-v0 pipeline smoke (fake engine, real gate, no keys/Docker) — plan §10 P3 local half")
    t = Tally("P3 local pipeline")
    scratch = empty_dir("groundwork_pipeline")

    print("[1/8] toolchain")
    gw.ensure_binaries(t, scratch)
    print("[2/8] materialize corpus (builder --out / --solutions)")
    expt, sols = gw.build_corpus(scratch)
    print("[3/8] author §6 2-arm experiment (grounded vs bare) + plan")
    ws = author_experiment(expt)
    ws.plan(actor="shakedown")
    locked = ws.view().by_kind("experiment_locked")
    t.check("experiment locked (§6-shaped: grounded/bare payload asymmetry)", len(locked) == 1,
            "one experiment_locked event")
    print("[4/8] run (fake engine)")
    ws.run(engine="fake")
    n_trials = len(ws.view().trials())
    print(f"    {n_trials} trials")
    print("[5/8] materialize per-arm trees (grounded→solution, bare→exemplar-violation)")
    n_mat = gw.materialize_arms(ws, sols)
    print(f"    materialized {n_mat} trial workspaces")
    print("[6/8] grade (local-exec ADVISORY tier — real flowmap+groundwork+wrapper, per trial)")
    n_graded = gw.grade_localexec(ws, expt)
    print(f"    graded {n_graded} trials")
    print("[7/8] judge → selfcheck → analyze --exploratory")
    ws.judge()
    ws.selfcheck(actor="shakedown")
    ws.analyze(exploratory=True)
    print("[8/8] verify-chain + artifact checks")
    chain = ws.verify_chain()

    gw.discrimination_table(t, ws)

    md = expt / "findings.exploratory.md"
    dossier = expt / "findings.exploratory.dossier.html"
    fjson = expt / "findings.json"
    t.check("ledger verify-chain green", chain.chain_ok, "chain OK")
    t.check("findings.exploratory.* rendered", md.exists() and fjson.exists(),
            "findings.exploratory.md + findings.json exist")
    t.check("dossier written", dossier.exists(), "findings.exploratory.dossier.html exists")

    if args.plant_funnel_fixtures:
        print("\n[funnel] --plant-funnel-fixtures")
        gw.plant_and_run_funnel(t, ws, expt)

    print(f"\n    experiment dir: {expt}")
    print(f"    artifacts: experiment.yaml, tasks.yaml, ledger.ndjson, holdouts/, "
          f"findings.exploratory.(md|json|dossier.html)")
    t.finish()


if __name__ == "__main__":
    main()
