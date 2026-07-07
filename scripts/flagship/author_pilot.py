#!/usr/bin/env python3
"""Author the harbor CALIBRATION PILOT deterministically (verdi-go plan §6 / §10 P3–P4).

The pilot the owner runs FIRST (human decision D4, 2026-07-07): a small, cheap,
grade-only harbor run under a cost ceiling, whose two jobs are

  1. **CalibrationVariance** — haiku bare-vs-grounded over a stratified subset
     (covering all four corpus classes) at ``reps`` repetitions, feeding
     ``plan/power.py``'s MDE gate at flagship-lock time; and
  2. **cost-per-opus-trial** — a small opus bare-vs-grounded slice whose metering
     supplies the expensive number that drives the D4 2×2-vs-staged decision.

Why TWO experiments, not one 4-arm spec: verdi-bench arms span ALL of an
experiment's tasks, so "haiku over a stratified subset PLUS opus over a *small*
slice" cannot be one 4-arm experiment (that would run the two expensive Opus arms
over every subset task). The faithful realization is two 2-arm experiments,
written under ``<out>/calibration-haiku/`` and ``<out>/opus-cost-slice/``, sharing
one ``--ceiling`` budget. (Judgment call — surfaced in the summary.)

The pilot SKIPS the judge: grade-only calibration needs none (judging is
idempotent and costs money), and judge_preference calibration belongs to the
flagship. The schema still demands a judge field, so a never-invoked placeholder
(``fake/deterministic-*``) is used and the pilot sequence simply never runs the
``judge`` verb (runbook step 6). The REAL OpenAI judge (D5) is resolved at the
flagship (author_flagship ``--judge-model``).

The pilot design SCALES with ``--ceiling`` (default 10; the owner may run at 50):
a bigger ceiling buys a fuller subset, then 2 reps, then a larger opus slice — a
better CalibrationVariance. The projected-cost table is printed, and a design
whose (conservative, estimated) projection exceeds the ceiling is REFUSED loudly
(``costmodel.CeilingTooLowError``). All costs are ESTIMATES the pilot replaces.

    uv run python scripts/flagship/author_pilot.py --corpus-out <build_tasks --out dir> \
        --out <pilot dir> [--ceiling 10] [--seed 1234]

``--corpus-out`` is the directory ``corpora/groundwork-v0/build_tasks.py --out``
emitted (``make corpus-groundwork-v0`` → ``scratch/groundwork-v0/expt``): its
``tasks.yaml`` + ``holdouts/`` are consumed read-only.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                                   # sibling: costmodel
sys.path.insert(0, str(_HERE.parents[1] / "scripts" / "shakedown"))  # shared corpus lib

import costmodel  # noqa: E402
import _groundwork_lib as gw  # noqa: E402

DEFAULT_SEED = 1234
DEFAULT_CEILING = 10.0
# Both model-API hosts on the metering allowlist (D5). The pilot only reaches
# api.anthropic.com (all arms are claude_code); api.openai.com rides along so the
# same run.config is valid once the flagship adds the OpenAI judge.
ALLOWLIST = ["api.anthropic.com", "api.openai.com"]
CALIBRATION_DIR = "calibration-haiku"
OPUS_SLICE_DIR = "opus-cost-slice"


@dataclass(frozen=True)
class PilotResult:
    design: costmodel.PilotDesign
    haiku_ids: list[str]
    opus_ids: list[str]
    haiku_dir: Path
    opus_dir: Path
    ceilings: dict[str, float]


def _pilot_run_config() -> dict:
    keys = {gw.GROUNDED: ["ANTHROPIC_API_KEY"], gw.BARE: ["ANTHROPIC_API_KEY"]}
    return gw.run_config(allowlist=ALLOWLIST, keys_by_arm=keys)


def _author_one(sub_out: Path, corpus_out: Path, *, name: str, model: str, seed: int,
                ceiling: float, judge: str, reps: int, ids: list[str], task_dicts: list[dict],
                trial_image: Optional[str] = None):
    """Author one 2-arm pilot experiment (grounded vs bare) over ``ids`` into
    ``sub_out``: copy the selected holdouts, build via the SDK, attach the managed-
    proxy run.config, and write. Returns the ``ExperimentWorkspace``."""
    gw.copy_holdouts(corpus_out, sub_out, ids)
    exp = gw.build_two_arm(name, model=model, seed=seed, ceiling=ceiling, judge=judge, reps=reps)
    gw.add_corpus_tasks(exp, task_dicts, ids=ids, image=trial_image)
    exp.run_config(_pilot_run_config())
    return exp.write(sub_out)


def author_pilot(corpus_out, out, *, ceiling: float = DEFAULT_CEILING, seed: int = DEFAULT_SEED,
                 judge: str = gw.PLACEHOLDER_JUDGE, trial_image: Optional[str] = None,
                 quiet: bool = False) -> PilotResult:
    """Plan + author the two pilot experiments. Deterministic given identical inputs.

    Raises :class:`costmodel.CeilingTooLowError` when even the minimal design's
    conservative projection exceeds ``ceiling``."""
    corpus_out, out = Path(corpus_out), Path(out)
    task_dicts = gw.load_corpus_tasks(corpus_out)
    n_corpus = len(task_dicts)

    design = costmodel.plan_pilot(
        ceiling, n_corpus=n_corpus,
        cost_haiku_trial=costmodel.est_cost_per_trial("haiku"),
        cost_opus_trial=costmodel.est_cost_per_trial("opus"),
    )

    # Seeded, class-covering selection: the haiku subset covers all four classes
    # (subset >= 4, one per class); the opus slice round-robins the same class
    # order (smallest slice = a binding trap + the null). Namespaced sub-seeds so
    # the two selections do not share a draw.
    from harness.plan.seeds import sub_seed

    haiku_ids = gw.select_stratified(task_dicts, design.haiku_subset, seed=sub_seed(seed, "pilot-haiku"))
    opus_ids = gw.select_stratified(task_dicts, design.opus_slice, seed=sub_seed(seed, "pilot-opus"))

    # Each experiment's cost_ceiling: haiku gets exactly its (conservative) projection
    # — the gross-input estimate runs ABOVE the cached real cost, so the calibration
    # completes; the opus slice gets the rest of the budget (>= its projection),
    # absorbing the ceiling slack and doubling as the runaway-opus stop. Sum == ceiling.
    haiku_ceiling = round(design.haiku_projected, 4)
    opus_ceiling = round(ceiling - design.haiku_projected, 4)

    haiku_dir = out / CALIBRATION_DIR
    opus_dir = out / OPUS_SLICE_DIR
    _author_one(haiku_dir, corpus_out, name="groundwork-flagship-pilot-haiku",
                model=gw.MODEL_HAIKU, seed=sub_seed(seed, "pilot-haiku"), ceiling=haiku_ceiling,
                judge=judge, reps=design.reps, ids=haiku_ids, task_dicts=task_dicts,
                trial_image=trial_image)
    _author_one(opus_dir, corpus_out, name="groundwork-flagship-pilot-opus-slice",
                model=gw.MODEL_OPUS, seed=sub_seed(seed, "pilot-opus"), ceiling=opus_ceiling,
                judge=judge, reps=1, ids=opus_ids, task_dicts=task_dicts,
                trial_image=trial_image)

    result = PilotResult(design=design, haiku_ids=haiku_ids, opus_ids=opus_ids,
                         haiku_dir=haiku_dir, opus_dir=opus_dir,
                         ceilings={CALIBRATION_DIR: haiku_ceiling, OPUS_SLICE_DIR: opus_ceiling})
    if not quiet:
        _print_summary(result, corpus_out, task_dicts, judge, seed)
    return result


def _print_summary(r: PilotResult, corpus_out: Path, task_dicts: list[dict], judge: str, seed: int) -> None:
    hc = gw.classes_of(task_dicts, r.haiku_ids)
    oc = gw.classes_of(task_dicts, r.opus_ids)
    print(r.design.table())
    print()
    print(f"  seed={seed}   judge={judge!r}  (NEVER INVOKED — grade-only pilot; real OpenAI judge at flagship, D5)")
    print(f"  calibration-haiku : {gw.MODEL_HAIKU}")
    print(f"      subset ({len(r.haiku_ids)}) covers classes {sorted(hc)} : {r.haiku_ids}")
    print(f"      cost_ceiling ${r.ceilings[CALIBRATION_DIR]:.2f}  dir={r.haiku_dir}")
    print(f"  opus-cost-slice   : {gw.MODEL_OPUS}")
    print(f"      slice ({len(r.opus_ids)}) covers classes {sorted(oc)} : {r.opus_ids}")
    print(f"      cost_ceiling ${r.ceilings[OPUS_SLICE_DIR]:.2f}  dir={r.opus_dir}")
    print(f"  union covers all four classes: {sorted(hc | oc)}")
    print("\n  NEXT (runbook step 6): plan --actor / run --engine harbor / grade --runner docker /")
    print("  `bench corpus calibrate` each dir; read per-trial cost from the metering attribution.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-out", type=Path, required=True,
                    help="the `build_tasks.py --out` directory (tasks.yaml + holdouts/)")
    ap.add_argument("--out", type=Path, required=True, help="target pilot directory")
    ap.add_argument("--ceiling", type=float, default=DEFAULT_CEILING,
                    help=f"pilot cost ceiling in USD (default {DEFAULT_CEILING}; e.g. 50 for a fuller pilot)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"seed (default {DEFAULT_SEED})")
    ap.add_argument("--judge-model", default=gw.PLACEHOLDER_JUDGE,
                    help="placeholder judge id (never invoked in the pilot; must be schema-valid)")
    ap.add_argument("--trial-image", default=None,
                    help="digest-pinned claude-code-groundwork image ref for the harbor run "
                         "(from runbook step 2); omit for a fake-engine dry run")
    args = ap.parse_args()
    try:
        author_pilot(args.corpus_out, args.out, ceiling=args.ceiling, seed=args.seed,
                     judge=args.judge_model, trial_image=args.trial_image)
    except costmodel.CeilingTooLowError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
