#!/usr/bin/env python3
"""Author the FLAGSHIP experiment from the pilot's measured outputs (plan §6 / §10 P4).

The separately-invoked, full-benchmark path — run once the owner is funded and has
the pilot numbers in hand. It consumes the calibration pilot's RESULTS and:

  (a) computes MDE-driven **repetitions** via the real ``plan/power.py`` seams —
      ``CalibrationVariance`` from the pilot when available, else ``AssumedVariance``
      with the ``assumption_based_mde`` flag surfaced loudly;
  (b) **projects total spend** for the locked 2×2 and for the staged haiku-first
      design from the owner-supplied per-trial costs (the pilot's metering);
  (c) prints a **D4 decision table** — the human decision (2026-07-07): the locked
      2×2 ({opus,haiku}×{bare,grounded}, ``multi_arm_correction: holm``) IFF the
      projected 2×2 spend ≤ ``--flagship-ceiling``; otherwise the staged 2-arm
      (haiku ± grounded) as the first official run;
  (d) **authors** the chosen design's experiment (payload asymmetry, seed,
      ceiling, the MDE-driven reps, the MDE-backed decision threshold, and — so the
      plan power gate can enforce it — ``hypothesized_effect``), with the judge set
      to ``openai/<exact-id>`` passed via ``--judge-model`` (REQUIRED; the
      ``openai/`` prefix is validated and the id is never defaulted — D5).

Deterministic given identical inputs. The judge id is resolved at lock time from
the owner's available OpenAI GPT-5.x models; this kit NEVER invents one — the
placeholder convention is documented in the runbook.

    uv run python scripts/flagship/author_flagship.py \
        --corpus-out <build_tasks --out dir> --out <flagship dir> \
        --judge-model openai/<exact-versioned-id> --flagship-ceiling <usd> \
        --cost-per-trial-haiku <usd> --cost-per-trial-opus <usd> \
        [--pilot-manifest <corpus-manifest.json>] [--target-mde 0.2] [--seed 1234]

Per-trial costs come from the pilot's metering attribution (runbook step 6); they
are required, not auto-guessed, so the projection is honest and reproducible.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1] / "scripts" / "shakedown"))

import costmodel  # noqa: E402
import _groundwork_lib as gw  # noqa: E402

DEFAULT_SEED = 1234
DEFAULT_TARGET_MDE = 0.20   # detect a 20-percentage-point uplift on the trap-class contrast
DEFAULT_MAX_REPS = 10
DEFAULT_RHO = 0.3           # recorded within-task correlation assumption (matches corpus_calibrate)
ALLOWLIST = ["api.anthropic.com", "api.openai.com"]  # arms + OpenAI judge (D5)

# The two candidate designs. Same-model bare-vs-grounded pairs (the payload
# asymmetry IS the treatment); the 2×2 crosses model tier × groundwork access.
ARMS_2X2 = [
    ("opus-bare", gw.MODEL_OPUS, gw.BARE_PAYLOAD),
    ("opus-grounded", gw.MODEL_OPUS, gw.GROUNDED_PAYLOAD),
    ("haiku-bare", gw.MODEL_HAIKU, gw.BARE_PAYLOAD),
    ("haiku-grounded", gw.MODEL_HAIKU, gw.GROUNDED_PAYLOAD),
]
ARMS_STAGED = [
    ("haiku-bare", gw.MODEL_HAIKU, gw.BARE_PAYLOAD),
    ("haiku-grounded", gw.MODEL_HAIKU, gw.GROUNDED_PAYLOAD),
]


class JudgeModelError(ValueError):
    """--judge-model is absent, not OpenAI-prefixed, or not a versioned id (D5)."""


def validate_judge_model(model: Optional[str]) -> str:
    """Fail LOUD on a judge id that is not ``openai/<versioned-id>`` (D5). The
    ``openai/`` prefix is checked here; the schema additionally rejects an
    un-versioned alias (``openai/gpt-5``) at ``.build()`` — a second, independent
    guard. Never defaulted: an absent value is a refusal, not a silent pick."""
    if not model:
        raise JudgeModelError(
            "--judge-model is REQUIRED (D5: the OpenAI GPT-5.x judge). Pass the exact "
            "fully-versioned id from your available models, e.g. "
            "'openai/gpt-5.1-2025-XX-XX'. This kit never invents a judge id.")
    if not model.startswith("openai/"):
        raise JudgeModelError(
            f"--judge-model {model!r} is not an OpenAI id: D5 fixes the judge vendor to "
            "the OpenAI GPT-5.x family. Use 'openai/<exact-versioned-id>'.")
    return model


@dataclass(frozen=True)
class _MdeSpec:
    """Minimal ``spec`` shape ``mde_check`` reads: a seed + a repetitions default."""

    seed: int
    repetitions: int


def load_variance(args, *, n_tasks: int):
    """Resolve the variance source for the MDE gate [PL-5]: the pilot's real
    ``CalibrationVariance`` when a manifest carrying a calibration run is given,
    else an explicit ``--cal-p``, else ``AssumedVariance`` (flagged). Returns
    ``(variance_source, assumption_based)``."""
    from harness.plan.power import (AssumedVariance, CalibrationVariance,
                                    calibration_variance_from_runs)

    if args.pilot_manifest is not None:
        from harness.corpus.registry import CorpusManifest

        manifest = CorpusManifest.load(args.pilot_manifest)
        cv = calibration_variance_from_runs(manifest.calibration.runs)
        if cv is None:
            raise SystemExit(
                f"--pilot-manifest {args.pilot_manifest} has no calibration run carrying "
                "p/rho/n_tasks; run `bench corpus calibrate` on the pilot first, or pass "
                "--cal-p explicitly.")
        return cv, False
    if args.cal_p is not None:
        return CalibrationVariance(p=args.cal_p, rho=args.rho,
                                   n_tasks=args.cal_n if args.cal_n is not None else n_tasks), False
    return AssumedVariance(p=0.5, rho=args.rho, n_tasks=n_tasks), True


def recommend_reps(variance_source, *, n_tasks: int, seed: int, target_mde: float,
                   max_reps: int, n_sim: int, n_boot: int):
    """Sweep repetitions 1..max_reps and return ``(chosen, achieved_mde, curve)``:
    the smallest reps whose MDE (at 80% power, the same paired-bootstrap decision
    EVAL-6 uses) is <= ``target_mde``, or ``(None, None, curve)`` if the design
    cannot detect ``target_mde`` within the sweep. Deterministic (seeded sim)."""
    from harness.plan.power import mde_check

    curve = []
    chosen: Optional[int] = None
    achieved: Optional[float] = None
    for r in range(1, max_reps + 1):
        rep = mde_check(_MdeSpec(seed=seed, repetitions=r), variance_source,
                        n_tasks=n_tasks, repetitions=r, n_sim=n_sim, n_boot=n_boot)
        curve.append((r, rep.mde, list(rep.flags)))
        if chosen is None and rep.mde is not None and rep.mde <= target_mde:
            chosen, achieved = r, rep.mde
    return chosen, achieved, curve


def _keys_by_arm(arms) -> dict[str, list[str]]:
    # Every flagship arm is a claude_code / anthropic arm → ANTHROPIC_API_KEY. The
    # OpenAI judge key is NOT here (host-process env; see the runbook).
    return {name: ["ANTHROPIC_API_KEY"] for name, _model, _payload in arms}


def _build_experiment(name: str, arms, *, judge: str, seed: int, ceiling: float, reps: int,
                      threshold: float, correction: Optional[str], task_dicts: list[dict],
                      trial_image: Optional[str] = None):
    from harness.sdk import Experiment

    exp = Experiment(name, seed=seed, cost_ceiling_usd=ceiling)
    for arm_name, model, payload in arms:
        exp.arm(arm_name, model=model, platform="claude_code", payload=dict(payload))
    exp.judge(judge)
    exp.corpus("groundwork-v0", "0.0.0")
    exp.repetitions(reps)
    exp.decision(metric="holdout_pass_rate", op=">=", threshold=threshold)
    if correction is not None:
        exp.multi_arm_correction(correction)
    exp.run_config(gw.run_config(allowlist=ALLOWLIST, keys_by_arm=_keys_by_arm(arms)))
    gw.add_corpus_tasks(exp, task_dicts, image=trial_image)  # all corpus tasks
    return exp


def _inject_hypothesized_effect(exp_dir: Path, target_mde: float) -> None:
    """Set ``hypothesized_effect`` on the written (pre-lock) experiment.yaml so the
    plan power gate enforces the power target [PL-1, D001]. The builder has no
    setter for it, so it is injected by a VALIDATED round-trip: load the SDK-written
    yaml, add the key, re-validate through ``ExperimentSpec``, and re-serialize with
    the same ``spec_to_yaml`` the builder used (byte-deterministic). Pre-lock only —
    no ledger exists yet, so nothing is being rewritten under a lock."""
    import yaml

    from harness.schema.experiment import ExperimentSpec
    from harness.schema.serialize import spec_to_yaml

    path = exp_dir / "experiment.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["hypothesized_effect"] = round(target_mde, 6)
    spec = ExperimentSpec.from_dict(data)  # re-validate (0 < target_mde <= 1 enforced)
    path.write_text(spec_to_yaml(spec), encoding="utf-8")


@dataclass(frozen=True)
class FlagshipResult:
    chosen: str
    reps: int
    reps_from_mde: bool
    achieved_mde: Optional[float]
    projection: costmodel.FlagshipProjection
    variance_kind: str
    assumption_based: bool
    out: Path
    name: str


def author_flagship(corpus_out, out, *, judge_model: str, flagship_ceiling: float,
                    cost_per_trial_haiku: float, cost_per_trial_opus: float,
                    target_mde: float = DEFAULT_TARGET_MDE, max_reps: int = DEFAULT_MAX_REPS,
                    seed: int = DEFAULT_SEED, rho: float = DEFAULT_RHO,
                    pilot_manifest=None, cal_p: Optional[float] = None,
                    cal_n: Optional[int] = None, cost_per_judgment: float = 0.0,
                    trial_image: Optional[str] = None, n_sim: int = 120, n_boot: int = 300,
                    quiet: bool = False) -> FlagshipResult:
    """Read the pilot outputs, decide 2×2-vs-staged (D4), and author the chosen
    flagship experiment. Deterministic given identical inputs."""
    corpus_out, out = Path(corpus_out), Path(out)
    validate_judge_model(judge_model)
    if not (0.0 < target_mde <= 1.0):
        raise ValueError(f"--target-mde must be in (0, 1]; got {target_mde}")

    task_dicts = gw.load_corpus_tasks(corpus_out)
    n_tasks = len(task_dicts)

    args = argparse.Namespace(pilot_manifest=pilot_manifest, cal_p=cal_p, cal_n=cal_n, rho=rho)
    variance, assumption_based = load_variance(args, n_tasks=n_tasks)
    chosen_reps, achieved, curve = recommend_reps(
        variance, n_tasks=n_tasks, seed=seed, target_mde=target_mde,
        max_reps=max_reps, n_sim=n_sim, n_boot=n_boot)
    reps = chosen_reps if chosen_reps is not None else max_reps
    reps_from_mde = chosen_reps is not None

    projection = costmodel.project_flagship(
        n_tasks=n_tasks, reps=reps, cost_haiku_trial=cost_per_trial_haiku,
        cost_opus_trial=cost_per_trial_opus, flagship_ceiling=flagship_ceiling,
        cost_per_judgment=cost_per_judgment)
    chosen = costmodel.decide_d4(projection)

    if chosen == "2x2":
        arms, correction, name = ARMS_2X2, "holm", "groundwork-flagship-2x2"
    else:
        arms, correction, name = ARMS_STAGED, "none", "groundwork-flagship-staged-haiku"

    exp = _build_experiment(name, arms, judge=judge_model, seed=seed,
                            ceiling=flagship_ceiling, reps=reps, threshold=target_mde,
                            correction=correction, task_dicts=task_dicts, trial_image=trial_image)
    exp.write(out)                                   # SDK build path (validates)
    gw.copy_holdouts(corpus_out, out, [d["id"] for d in task_dicts])
    _inject_hypothesized_effect(out, target_mde)     # power target for the plan gate

    result = FlagshipResult(
        chosen=chosen, reps=reps, reps_from_mde=reps_from_mde, achieved_mde=achieved,
        projection=projection, variance_kind=type(variance).__name__,
        assumption_based=assumption_based, out=out, name=name)
    if not quiet:
        _print_summary(result, curve, target_mde, judge_model, seed)
    return result


def _print_summary(r: FlagshipResult, curve, target_mde: float, judge: str, seed: int) -> None:
    print("MDE / power (plan/power.py):")
    print(f"  variance source: {r.variance_kind}" + (
        "   [!! assumption_based_mde — NO pilot calibration; the flag rides the lock and findings]"
        if r.assumption_based else "   [CalibrationVariance from the pilot]"))
    print(f"  target MDE = {target_mde}   repetitions sweep (reps -> MDE):")
    for reps, mde, flags in curve:
        mark = "  <== chosen" if reps == r.reps and r.reps_from_mde else ""
        print(f"    reps {reps:2d} -> MDE {('n/a' if mde is None else f'{mde:.3f}')}"
              f"{('  flags=' + ','.join(flags)) if flags else ''}{mark}")
    if r.reps_from_mde:
        print(f"  => MDE-driven repetitions = {r.reps} (achieved MDE {r.achieved_mde:.3f} <= target {target_mde})")
    else:
        print(f"  => target {target_mde} NOT reachable within {r.reps} reps at this variance/N; "
              f"using reps={r.reps}. hypothesized_effect={target_mde} is emitted, so `bench plan` will "
              "REFUSE as underpowered unless --acknowledge-underpowered (fail-closed).")
    print()
    print(costmodel.d4_table(r.projection))
    print()
    print(f"  authored: {r.name}  ({len(ARMS_2X2) if r.chosen == '2x2' else len(ARMS_STAGED)} arms, "
          f"reps={r.reps}, holm={'yes' if r.chosen == '2x2' else 'no'})")
    print(f"  judge={judge!r}   seed={seed}   cost_ceiling=${r.projection.flagship_ceiling:.2f}")
    print(f"  decision_rule: delta_holdout_pass_rate >= {target_mde}   hypothesized_effect: {target_mde}")
    print(f"  dir={r.out}")
    print("\n  NEXT (runbook step 7 — LOCK CEREMONY): add §6 interpretation notes, contamination probe,")
    print("  selfcheck, then plan --actor / run / grade / judge / forensics / analyze --official + card emit.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-out", type=Path, required=True,
                    help="the `build_tasks.py --out` directory (tasks.yaml + holdouts/)")
    ap.add_argument("--out", type=Path, required=True, help="target flagship directory")
    ap.add_argument("--judge-model", required=True,
                    help="REQUIRED OpenAI judge id 'openai/<exact-versioned-id>' (D5); never defaulted")
    ap.add_argument("--flagship-ceiling", type=float, required=True,
                    help="the flagship cost ceiling in USD the owner sets after the pilot")
    ap.add_argument("--cost-per-trial-haiku", type=float, required=True,
                    help="measured $/trial for the haiku tier (from the pilot metering)")
    ap.add_argument("--cost-per-trial-opus", type=float, required=True,
                    help="measured $/trial for the opus tier (from the pilot metering)")
    ap.add_argument("--target-mde", type=float, default=DEFAULT_TARGET_MDE,
                    help=f"effect the design must detect, in (0,1] (default {DEFAULT_TARGET_MDE})")
    ap.add_argument("--max-reps", type=int, default=DEFAULT_MAX_REPS,
                    help=f"repetitions sweep bound (default {DEFAULT_MAX_REPS})")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"seed (default {DEFAULT_SEED})")
    ap.add_argument("--rho", type=float, default=DEFAULT_RHO,
                    help=f"within-task correlation assumption (default {DEFAULT_RHO})")
    ap.add_argument("--pilot-manifest", type=Path, default=None,
                    help="corpus manifest carrying the pilot's `bench corpus calibrate` run (CalibrationVariance)")
    ap.add_argument("--cal-p", type=float, default=None,
                    help="explicit calibration per-arm success rate (bypasses --pilot-manifest)")
    ap.add_argument("--cal-n", type=int, default=None,
                    help="explicit calibration n_tasks (default: the corpus size)")
    ap.add_argument("--cost-per-judgment", type=float, default=0.0,
                    help="OPTIONAL judge $/judgment add-on for the projection (default 0 — advisory, run once)")
    ap.add_argument("--trial-image", default=None,
                    help="digest-pinned claude-code-groundwork image ref for the harbor run "
                         "(from runbook step 2); omit for a fake-engine dry run")
    args = ap.parse_args()
    try:
        author_flagship(
            args.corpus_out, args.out, judge_model=args.judge_model,
            flagship_ceiling=args.flagship_ceiling,
            cost_per_trial_haiku=args.cost_per_trial_haiku,
            cost_per_trial_opus=args.cost_per_trial_opus, target_mde=args.target_mde,
            max_reps=args.max_reps, seed=args.seed, rho=args.rho,
            pilot_manifest=args.pilot_manifest, cal_p=args.cal_p, cal_n=args.cal_n,
            cost_per_judgment=args.cost_per_judgment, trial_image=args.trial_image)
    except JudgeModelError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
