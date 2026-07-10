#!/usr/bin/env python3
"""Author the consistency-RECONNAISSANCE experiment (verdi-go plan §6 / §10 P4).

The recon is the FIRST stage of the consistency program: one grade-only harbor
run of the WHOLE groundwork-v0 corpus at a single tier (haiku), bare vs grounded,
repeated ``--reps`` times, under a cost fence. It exists to MEASURE — not to test a
hypothesis — the calibration inputs a pre-registered consistency experiment needs:

  * per-task, per-rep BARE failure structure (which tasks fail, how often);
  * rep CLUSTERING (the within-task correlation ρ the MDE/power model consumes);
  * the INTERMITTENT-task set (tasks that pass some reps and fail others); and
  * the EXACT per-trial cost, from the run's own native ``modelUsage`` telemetry,
    which SUPERSEDES the conservative estimates this script prints.

It reuses the shared corpus plumbing exactly the way ``author_pilot.py`` does
(``_groundwork_lib``: corpus load, holdout copy, managed-proxy run.config; the
two-arm build itself is inline here — the grounded payload is rung-dependent)
and the flagship cost model's estimates (``costmodel``). Unlike
the pilot — two experiments over subsets — the recon is ONE experiment over ALL 17
tasks written directly AT ``--out``; it REFUSES loudly rather than silently subset
a short corpus, and REFUSES (no partial write) when the projected spend overruns
``--ceiling``. Same posture as the pilot's calibration experiment: same payload
asymmetry (grounded carries the tool, bare is empty), a never-invoked placeholder
judge (grade-only — no judge is run), and the managed metering proxy.

CROSS-MODEL BASELINE (owner requirement): ``--model`` re-authors the IDENTICAL
experiment at another tier — same corpus, seed, reps, payload asymmetry, and
ceiling semantics; ONLY the arm model (and the tier-derived ``<tier>-bare`` /
``<tier>-grounded`` arm names) change. Pricing uses ``costmodel``'s matching
per-tier constant when one exists (haiku, opus); a tier without one projects
UNKNOWN and REQUIRES an explicit ``--ceiling`` — a model is never priced by
guesswork. A model id whose tier token cannot be derived is refused loudly.

TREATMENT RUNG (owner decision, rung-2 approval): ``--workflow`` is REQUIRED —
the rung must be explicit in every authoring command, never defaulted.
``availability`` (rung 1) arms the grounded payload ``{"tools": ["groundwork"]}``
only; ``ground_verify`` (rung 2, "instructed") adds ``workflow: ground_verify``,
which the trial agent maps to the pre-registered ``--append-system-prompt``
instruction; ``ground_verify_enforced`` (rung 3, "enforced") adds ``workflow:
ground_verify_enforced``, which the trial agent maps to rung 2's argv PLUS an
enforcement Stop hook armed in arm-time filesystem. The grounded payload is built
HERE (not from ``_groundwork_lib.GROUNDED_PAYLOAD`` — ``author_pilot``'s §6
semantics stay untouched). Everything else is identical per rung+model.

MECHANISM-DECOMPOSITION treatments (design:
``docs/design/mechanism-decomposition-program.md``) isolate WHICH part of the
enforced rung earns the effect: ``placebo_gate`` carries rung 3's payload shape but
the placebo workflow, arming a static-reason Stop hook (all mechanics of enforcement,
none of the grounding signal). ``policy_pointer`` is PROMPT-ONLY — it stages no tool
at all, only a ``system_prompt_extra`` pointer the trial agent maps to a pre-registered
prompt. Their treatment arms carry HONEST names — ``<tier>-placebo`` / ``<tier>-pointer``
— because an arm labeled ``grounded`` that stages no tool would be a mislabeled
condition; the three historical rungs keep ``<tier>-grounded`` byte-identically.

An OPTIONAL ``--tasks`` authors an EXPLICIT task-id subset (each validated against
the corpus; unknown/empty refused loudly, no partial write); omitted, it authors all
17 byte-identically. The corpus is still verified to be the full 17 either way.

    uv run python scripts/flagship/author_consistency.py \
        --corpus-out <build_tasks --out dir> --out runs/consistency/recon \
        --trial-image sha256:<digest> \
        --workflow {availability,ground_verify,ground_verify_enforced} \
        [--tasks gw-r1,gw-o2] [--reps 5] [--ceiling 35] \
        [--model anthropic/claude-haiku-4-5-20251001]

``--corpus-out`` is the directory ``corpora/groundwork-v0/build_tasks.py --out``
emitted (``tasks.yaml`` + ``holdouts/``), consumed read-only.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                                   # sibling: costmodel
sys.path.insert(0, str(_HERE.parents[1] / "scripts" / "shakedown"))  # shared corpus lib

import costmodel  # noqa: E402
import _groundwork_lib as gw  # noqa: E402

DEFAULT_REPS = 5
DEFAULT_CEILING = 35.0
DEFAULT_SEED = 1234

# The recon is a tool A/B at a FIXED tier: both arms carry the SAME model (default
# MODEL_HAIKU; --model swaps the tier for the cross-model baseline), so the only
# declared difference is the payload (grounded carries the groundwork tool; bare is
# the empty control). Grounded is arm_a (the paired delta is arm_a - arm_b), matching
# the pilot's calibration experiment. Arm names are <tier>-bare / <tier>-grounded,
# with the tier token derived from the model id — never guessed.
RECON_NAME = "groundwork-consistency-recon"

# Both model-API hosts on the metering allowlist, mirroring the pilot: the recon
# only reaches api.anthropic.com (all arms are claude_code), but api.openai.com
# rides along so the same run.config stays valid if an OpenAI judge is ever added.
ALLOWLIST = ["api.anthropic.com", "api.openai.com"]

# The groundwork-v0 corpus task set, EXACTLY (6 reach / 4 obligation / 4 null / 3
# multi-impl = 17; mirrors corpora/groundwork-v0/tasks/ and gw.CLASS_ORDER). The
# recon runs ALL of them; a corpus that does not match this set is refused rather
# than silently subset — an intentional change here is a deliberate design edit.
EXPECTED_TASK_IDS = frozenset({
    "gw-r1", "gw-r2", "gw-r3", "gw-r4", "gw-r5", "gw-r5b",  # reach-trap (r5b: de-baited r5)
    "gw-o1", "gw-o2", "gw-o3", "gw-o4",                  # obligation-trap
    "gw-n1", "gw-n2", "gw-n3", "gw-n4",                  # null
    "gw-m1", "gw-m2", "gw-m3",                           # multi-impl
})


class ConsistencyRefusal(ValueError):
    """The recon design cannot be authored as requested — refused with no partial
    write (a short/mismatched corpus, an underivable/unpriced model without an
    explicit ceiling, an unknown workflow rung, or a projection over the ceiling)."""


# The grounded payload per treatment rung, built LOCALLY (never from
# ``_groundwork_lib.GROUNDED_PAYLOAD`` — author_pilot's §6 semantics stay
# untouched). availability arms the tool only; ground_verify adds the payload key
# the claude-code-groundwork agent maps to the pre-registered instructed
# system prompt (its WORKFLOW_SYSTEM_PROMPTS entry); ground_verify_enforced (rung 3)
# adds the enforcement Stop hook the agent arms in arm-time filesystem — argv
# identical to rung 2, the enforcement isolated.
GROUNDED_PAYLOADS_BY_WORKFLOW: dict[str, dict] = {
    "availability": {"tools": ["groundwork"]},
    "ground_verify": {"tools": ["groundwork"], "workflow": "ground_verify"},
    "ground_verify_enforced": {"tools": ["groundwork"], "workflow": "ground_verify_enforced"},
    # mechanism-decomposition treatments [design:
    # docs/design/mechanism-decomposition-program.md]: the placebo carries
    # rung 3's payload shape with the placebo workflow (the trial image swaps
    # the hook); the pointer is PROMPT-ONLY — no tools key at all (the image's
    # system_prompt_extra arming path; combining would be refused by the agent).
    "placebo_gate": {"tools": ["groundwork"], "workflow": "placebo_gate"},
    "policy_pointer": {"system_prompt_extra": "policy_pointer"},
}

# The treatment arm's name suffix per workflow. The three historical rungs stay
# "<tier>-grounded" BYTE-IDENTICALLY (re-authoring a historical experiment must
# reproduce it); the new treatments get honest names — an arm labeled
# "grounded" that stages no tool would be a mislabeled condition in every
# ledger event and report.
ARM_SUFFIX_BY_WORKFLOW: dict[str, str] = {
    "availability": "grounded",
    "ground_verify": "grounded",
    "ground_verify_enforced": "grounded",
    "placebo_gate": "placebo",
    "policy_pointer": "pointer",
}


def grounded_payload_for(workflow: str) -> dict:
    """The grounded arm's payload for ``workflow`` — a fresh copy, refused loudly
    for a rung outside the registered set (a typo must never author silently)."""
    if workflow not in GROUNDED_PAYLOADS_BY_WORKFLOW:
        raise ConsistencyRefusal(
            f"unknown workflow rung {workflow!r}; choose one of "
            f"{sorted(GROUNDED_PAYLOADS_BY_WORKFLOW)}"
        )
    return dict(GROUNDED_PAYLOADS_BY_WORKFLOW[workflow])


def derive_tier(model: str) -> str:
    """The model's tier token: the segment after ``claude-`` up to the next ``-``
    in ``<provider>/claude-<tier>-…`` (``haiku`` / ``sonnet`` / ``opus``).

    Arm names and pricing derive from this token, so an id it cannot be read from
    is REFUSED loudly — never guessed. A provider-less id is refused here too: the
    schema would reject it anyway (arm.model must be ``provider/id``), but only
    mid-write, and a refusal must leave no partial write."""
    bare = model.split("/", 1)[-1]
    tier = bare[len("claude-"):].split("-", 1)[0] if bare.startswith("claude-") else ""
    if "/" not in model or not tier:
        raise ConsistencyRefusal(
            f"cannot derive a tier token from model id {model!r}: expected "
            "'<provider>/claude-<tier>-…' (e.g. 'anthropic/claude-haiku-4-5-20251001'). "
            "Arm names and pricing are never guessed."
        )
    return tier


@dataclass(frozen=True)
class ReconDesign:
    """The fixed recon design + its projected spend (all figures ESTIMATES the run's
    own native telemetry replaces). ``cost_per_trial`` is ``None`` when ``costmodel``
    has no constant for the tier — the projection is then honestly UNKNOWN, never
    guessed, and authoring demands an explicit ceiling instead."""

    n_tasks: int
    reps: int
    tier: str
    cost_per_trial: Optional[float]
    ceiling: float

    @property
    def arms(self) -> int:
        return 2  # <tier>-bare vs <tier>-grounded, same model

    @property
    def trials(self) -> int:
        return self.n_tasks * self.reps * self.arms

    @property
    def projected(self) -> Optional[float]:
        """Conservative projection: tasks × reps × 2 × per-trial estimate (gross-input
        list price; real cost is LOWER once prompt caching bills the repeated prefix).
        ``None`` when the tier has no costmodel constant (UNKNOWN, not zero)."""
        if self.cost_per_trial is None:
            return None
        return round(self.trials * self.cost_per_trial, 4)

    @property
    def fits(self) -> bool:
        # An UNKNOWN projection cannot be fenced here; author_consistency requires
        # an EXPLICIT ceiling for it, and the spec's cost_ceiling guards the run.
        return self.projected is None or self.projected <= self.ceiling

    def table(self) -> str:
        if self.cost_per_trial is None:
            estimate = (f"  per-trial cost estimate:  UNKNOWN — no costmodel constant "
                        f"for tier {self.tier!r} (explicit --ceiling required)")
            projected_cell = f"{'UNKNOWN':>11s}"
            verdict = (f"  projected UNKNOWN  vs  ceiling ${self.ceiling:.2f}   -> "
                       "UNFENCED (the spec's cost_ceiling guards the run)")
        else:
            estimate = (f"  per-trial cost estimate:  {self.tier} ${self.cost_per_trial:.4f}  "
                        "(gross-input list price; caching bills lower)")
            projected_cell = f"{self.projected:11.2f}"
            verdict = (f"  projected ${self.projected:.2f}  vs  ceiling ${self.ceiling:.2f}   -> "
                       + ("OK" if self.fits else "OVER (REFUSE)"))
        rows = [
            f"consistency recon projection under ceiling ${self.ceiling:.2f}  "
            "(ESTIMATE — the run's own native modelUsage telemetry replaces it)",
            estimate,
            "  experiment          arms  tasks  reps  trials   projected$",
            "  ------------------  ----  -----  ----  ------  -----------",
            f"  consistency-recon      2  {self.n_tasks:5d}  {self.reps:4d}  {self.trials:6d}  "
            f"{projected_cell}",
            "  ------------------  ----  -----  ----  ------  -----------",
            verdict,
        ]
        return "\n".join(rows)


@dataclass(frozen=True)
class ReconResult:
    design: ReconDesign
    ids: list[str]
    out: Path
    model: str
    grounded_arm: str
    bare_arm: str
    workflow: str


def _assert_expected_corpus(ids: list[str]) -> None:
    """Refuse loudly unless ``ids`` is EXACTLY the expected corpus set — the recon
    must run the whole corpus, never a silent subset [plan §6]."""
    got = set(ids)
    if got != set(EXPECTED_TASK_IDS):
        missing = sorted(set(EXPECTED_TASK_IDS) - got)
        unexpected = sorted(got - set(EXPECTED_TASK_IDS))
        raise ConsistencyRefusal(
            f"consistency recon requires EXACTLY the {len(EXPECTED_TASK_IDS)} "
            f"groundwork-v0 corpus tasks (no silent subsetting): "
            f"missing={missing} unexpected={unexpected}. Rebuild the corpus "
            "(`make corpus-groundwork-v0`) or edit EXPECTED_TASK_IDS deliberately."
        )


def _select_task_ids(all_ids: list[str], tasks: Optional[list[str]]) -> list[str]:
    """The task ids to author: ALL of ``all_ids`` when ``tasks`` is None (the default —
    byte-identical to authoring the whole validated corpus), else EXACTLY the
    explicitly-named subset (sorted, deduped).

    An EXPLICIT subset is not silent subsetting: every requested id must exist in the
    (already corpus-validated) ``all_ids``; an unknown id — or an empty explicit
    selection — is refused loudly with :class:`ConsistencyRefusal`, before any write."""
    if tasks is None:
        return all_ids
    known = set(all_ids)
    unknown = sorted(t for t in tasks if t not in known)
    if unknown:
        raise ConsistencyRefusal(
            f"--tasks names id(s) not in the groundwork-v0 corpus: {unknown}; known "
            f"ids: {all_ids}. An explicit subset must name only real corpus tasks."
        )
    selected = sorted(set(tasks))
    if not selected:
        raise ConsistencyRefusal(
            "--tasks was given but selected no task ids; omit it to author all "
            f"{len(all_ids)}, or name at least one corpus task."
        )
    return selected


def author_consistency(corpus_out, out, *, trial_image: str, workflow: str,
                       model: str = gw.MODEL_HAIKU, reps: int = DEFAULT_REPS,
                       ceiling: Optional[float] = None, seed: int = DEFAULT_SEED,
                       judge: str = gw.PLACEHOLDER_JUDGE, quiet: bool = False,
                       tasks: Optional[list[str]] = None) -> ReconResult:
    """Plan + author the consistency-recon experiment. Deterministic given identical
    inputs; writes NOTHING under ``out`` on any refusal (wrong corpus, underivable
    model tier, unknown workflow rung, unknown task id, unpriced tier without an
    explicit ceiling, projection over the ceiling — :class:`ConsistencyRefusal` /
    :class:`costmodel.CeilingTooLowError` all raise before any write).

    ``workflow`` is REQUIRED (no default — the treatment rung must be an explicit
    decision in every authoring command): ``availability``, ``ground_verify``, or
    ``ground_verify_enforced``, selecting the grounded arm's payload from
    :data:`GROUNDED_PAYLOADS_BY_WORKFLOW`. ``ceiling=None`` means "defaulted"
    (``DEFAULT_CEILING``): allowed only for a tier ``costmodel`` prices — an
    unpriced tier under a defaulted ceiling would be priced by guesswork, so it is
    refused. ``model`` is the cross-model-baseline knob: everything except the arm
    model and the tier-derived arm names is identical across invocations of the
    same rung.

    ``tasks=None`` (the default) authors the WHOLE validated corpus, byte-identical to
    today; a list authors ONLY that explicit subset (each id validated against the
    corpus, unknown/empty refused loudly — an explicit subset is not silent
    subsetting). The corpus itself is still verified to be the full 17 either way."""
    corpus_out, out = Path(corpus_out), Path(out)
    payload = grounded_payload_for(workflow)  # refuses an unknown rung before any write
    tier = derive_tier(model)  # refuses an underivable id before any write
    grounded_arm = f"{tier}-{ARM_SUFFIX_BY_WORKFLOW[workflow]}"
    bare_arm = f"{tier}-bare"
    # Price by costmodel's matching constant when one exists (haiku, opus); an
    # unpriced tier projects UNKNOWN and demands an explicitly-chosen ceiling.
    cost = (costmodel.est_cost_per_trial(tier)
            if tier in costmodel.LIST_PRICE_PER_MTOK else None)
    if cost is None and ceiling is None:
        raise ConsistencyRefusal(
            f"no costmodel per-trial estimate exists for tier {tier!r} ({model}); a "
            f"defaulted ceiling (${DEFAULT_CEILING:.2f}) would price the recon by "
            "guesswork — pass an explicit --ceiling."
        )
    ceiling_usd = DEFAULT_CEILING if ceiling is None else ceiling

    task_dicts = gw.load_corpus_tasks(corpus_out)
    all_ids = sorted(d["id"] for d in task_dicts)
    _assert_expected_corpus(all_ids)  # corpus integrity: the full 17 — no write yet
    # An EXPLICIT --tasks subset (or all 17 when omitted); refuses an unknown/empty
    # selection loudly, before any write.
    ids = _select_task_ids(all_ids, tasks)

    design = ReconDesign(n_tasks=len(ids), reps=reps, tier=tier,
                         cost_per_trial=cost, ceiling=ceiling_usd)
    if not quiet:
        print(design.table())
    if not design.fits:
        raise costmodel.CeilingTooLowError(
            f"consistency recon ({design.n_tasks} tasks x {design.reps} reps x 2 arms "
            f"= {design.trials} trials) projects ${design.projected:.2f} > ceiling "
            f"${design.ceiling:.2f} at {tier} ${design.cost_per_trial:.4f}/trial. Raise "
            "--ceiling (the estimate is conservative — caching makes the real cost lower) "
            "or reduce --reps."
        )

    # Author only AFTER the fences pass, so a refusal leaves no partial write.
    from harness.plan.seeds import sub_seed
    from harness.sdk import Experiment

    gw.copy_holdouts(corpus_out, out, ids)
    # The §6 two-arm shape INLINE (not gw.build_two_arm): the grounded payload is
    # rung-dependent and built locally, and _groundwork_lib's GROUNDED_PAYLOAD /
    # build_two_arm stay untouched for author_pilot. Same arm order, platform,
    # corpus pin, and judge wiring — the ground_verify rung's output is
    # byte-identical to the pre-rung kit's.
    exp = (Experiment(RECON_NAME, seed=sub_seed(seed, "consistency-recon"),
                      cost_ceiling_usd=ceiling_usd)
           .arm(grounded_arm, model=model, platform="claude_code", payload=payload)
           .arm(bare_arm, model=model, platform="claude_code", payload={})
           .judge(judge)
           # corpus version 0.1.0: gw-r5b added [design: mechanism-decomposition piece 3]
           .corpus("groundwork-v0", "0.1.0")
           .repetitions(reps))
    gw.add_corpus_tasks(exp, task_dicts, ids=ids, image=trial_image)
    exp.run_config(_recon_run_config(bare_arm, grounded_arm))
    exp.write(out)

    result = ReconResult(design=design, ids=ids, out=out, model=model,
                         grounded_arm=grounded_arm, bare_arm=bare_arm,
                         workflow=workflow)
    if not quiet:
        _print_summary(result, judge, seed, trial_image)
    return result


def _recon_run_config(bare_arm: str, grounded_arm: str) -> dict:
    # bare-first insertion order keeps the emitted run.config.yaml byte-identical
    # to the pre-cross-model kit for the default (haiku) invocation.
    keys = {bare_arm: ["ANTHROPIC_API_KEY"], grounded_arm: ["ANTHROPIC_API_KEY"]}
    return gw.run_config(allowlist=ALLOWLIST, keys_by_arm=keys)


def _print_summary(r: ReconResult, judge: str, seed: int, trial_image: str) -> None:
    print()
    print(f"  authored: {RECON_NAME}  ({r.design.trials} trials, reps={r.design.reps})")
    print(f"  arms: {r.grounded_arm} / {r.bare_arm}  (both {r.model})")
    print(f"  workflow rung: {r.workflow}  "
          f"(grounded payload {GROUNDED_PAYLOADS_BY_WORKFLOW[r.workflow]})")
    subset = (f"  [EXPLICIT SUBSET of {len(EXPECTED_TASK_IDS)}]"
              if len(r.ids) != len(EXPECTED_TASK_IDS) else "")
    print(f"  tasks ({len(r.ids)}){subset}: {r.ids}")
    print(f"  cost_ceiling ${r.design.ceiling:.2f}   image={trial_image}")
    print(f"  seed={seed}   judge={judge!r}  (NEVER INVOKED — grade-only recon)")
    print(f"  dir={r.out}")
    print("\n  NEXT: plan --actor / run --engine harbor / grade --runner docker; then read")
    print("  per-task/rep pass structure + per-trial cost from the ledger + native modelUsage")
    print("  (the estimates above are SUPERSEDED by the run's own telemetry).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-out", type=Path, required=True,
                    help="the `build_tasks.py --out` directory (tasks.yaml + holdouts/)")
    ap.add_argument("--out", type=Path, required=True,
                    help="the experiment dir to write AT this path (e.g. runs/consistency/recon)")
    ap.add_argument("--reps", type=int, default=DEFAULT_REPS,
                    help=f"repetitions per task per arm (default {DEFAULT_REPS})")
    ap.add_argument("--ceiling", type=float, default=None,
                    help=f"recon cost ceiling in USD (default {DEFAULT_CEILING}); a model whose "
                         "tier has no costmodel estimate REQUIRES an explicit value")
    ap.add_argument("--model", default=gw.MODEL_HAIKU,
                    help=f"arm model id, provider/id (default {gw.MODEL_HAIKU}). The recon "
                         "re-authors IDENTICALLY per model — same corpus/seed/reps/payloads — "
                         "the cross-model baseline; only the arm model + tier names change")
    ap.add_argument("--workflow", required=True,
                    choices=sorted(GROUNDED_PAYLOADS_BY_WORKFLOW),
                    help="the treatment rung — REQUIRED, never defaulted: availability "
                         "(tool armed, no instruction) or ground_verify (adds the "
                         "pre-registered instructed system prompt). The mechanism "
                         "decomposition adds placebo_gate (a static-reason Stop hook — "
                         "enforcement mechanics, no grounding — authored as <tier>-placebo) "
                         "and policy_pointer (a PROMPT-ONLY system_prompt_extra, no tool, "
                         "authored as <tier>-pointer)")
    ap.add_argument("--trial-image", required=True,
                    help="digest-pinned claude-code-groundwork image ref for the harbor run")
    ap.add_argument("--tasks", default=None,
                    help="OPTIONAL comma-separated task-id subset to author (e.g. "
                         "gw-r1,gw-o2); each must exist in the corpus. Omitted authors "
                         "ALL 17 (byte-identical). An explicit subset is not silent "
                         "subsetting; an unknown id is refused with no partial write")
    args = ap.parse_args()
    tasks = None
    if args.tasks is not None:
        tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    try:
        author_consistency(args.corpus_out, args.out, trial_image=args.trial_image,
                           workflow=args.workflow, model=args.model, reps=args.reps,
                           ceiling=args.ceiling, tasks=tasks)
    except (ConsistencyRefusal, costmodel.CeilingTooLowError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
