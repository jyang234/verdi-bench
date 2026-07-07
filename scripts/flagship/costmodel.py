"""Cost model + pilot design ladder + D4 spend projection (flagship-bespoke).

The genuinely flagship-specific arithmetic the shared corpus plumbing
(``scripts/shakedown/_groundwork_lib``) deliberately does NOT own. Three pieces:

1. **The cost model** (``est_cost_per_trial``) — a documented, conservative
   per-trial token→dollar estimate. These are ESTIMATES to be *replaced* by the
   pilot's own metering; they exist only to size the pilot under a ceiling and to
   print an order-of-magnitude flagship projection. See the constants' comments.
2. **The pilot design ladder** (``plan_pilot``) — scales the calibration pilot
   with the ``--ceiling`` (owner directive): a minimal design at $10, a fuller
   subset + 2 reps + a larger opus slice as the ceiling rises. Refuses LOUDLY
   (``CeilingTooLowError``) when even the minimal design's projection exceeds the
   ceiling.
3. **The D4 spend projection** (``project_flagship`` / ``decide_d4``) — projects
   total spend for the locked 2×2 and for the staged haiku-first design and
   applies the D4 rule against the owner's flagship ceiling.

Pure arithmetic, no I/O, no wall clock — deterministic given identical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# The cost model — ESTIMATES, replaced by the pilot's measured metering.
# --------------------------------------------------------------------------- #
# Per-trial token profile for one agentic Go coding trial (Claude Code solving a
# one-function change in a stdlib-only module: read a few files, run `go test`,
# iterate a handful of turns). GROSS input tokens billed at the full input rate —
# a deliberately CONSERVATIVE (over-)estimate: prompt caching would bill repeated
# prefix at ~0.1x, so the real cost is LOWER. Over-estimating is the safe
# direction: it sizes the pilot SMALLER and trips the ceiling refusal SOONER.
# The whole point of the pilot is to replace these with measured per-trial cost.
EST_INPUT_TOKENS = 200_000
EST_OUTPUT_TOKENS = 20_000

# List price per 1M tokens, (input, output), from the Claude model catalog
# (claude-api skill, cached 2026-06-24). ESTIMATE: the owner's actual invoiced
# pricing governs, and the pilot's metering supersedes this table entirely.
#   opus  = anthropic/claude-opus-4-8-*   : $5 / $25
#   haiku = anthropic/claude-haiku-4-5-*  : $1 / $5
LIST_PRICE_PER_MTOK = {"opus": (5.00, 25.00), "haiku": (1.00, 5.00)}


def est_cost_per_trial(tier: str) -> float:
    """Estimated $/trial for ``tier`` ('opus' | 'haiku') under the documented
    conservative token profile. opus ≈ $1.50, haiku ≈ $0.30 at the defaults."""
    if tier not in LIST_PRICE_PER_MTOK:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(LIST_PRICE_PER_MTOK)}")
    p_in, p_out = LIST_PRICE_PER_MTOK[tier]
    return round(EST_INPUT_TOKENS / 1e6 * p_in + EST_OUTPUT_TOKENS / 1e6 * p_out, 4)


# --------------------------------------------------------------------------- #
# The pilot design ladder.
# --------------------------------------------------------------------------- #
# The calibration pilot is TWO experiments (the instrument's arms span ALL tasks,
# so "haiku over a stratified subset PLUS a small opus slice" is not one 4-arm
# spec — see author_pilot's module docstring):
#   * calibration-haiku : haiku bare-vs-grounded over a stratified subset (all
#     four classes), ``reps`` repetitions — the CalibrationVariance source.
#   * opus-cost-slice   : opus bare-vs-grounded over a small round-robin slice —
#     the cost-per-opus-trial measurement that drives the D4 decision.
# The minimal design (the $10 floor) and how it grows with the ceiling:
MIN_HAIKU_SUBSET = 4     # one task per class → covers all four classes
MIN_OPUS_SLICE = 2       # round-robin {reach-trap, null}: a binding trap + the null
MAX_REPS = 2             # 2 correlated reps give the clustered-variance signal the
#                          MDE model uses; >2 buys little for calibration.
OPUS_SLICE_CAP = 8       # a generous cap on opus exposure (cost-driver; sampled, not run in full)


class CeilingTooLowError(ValueError):
    """Even the minimal calibration pilot projects above the requested ceiling."""


@dataclass(frozen=True)
class PilotDesign:
    """The chosen pilot design + its projected spend (all figures ESTIMATES)."""

    haiku_subset: int
    reps: int
    opus_slice: int
    cost_haiku_trial: float
    cost_opus_trial: float
    ceiling: float
    # projected spend, split so each experiment gets its own cost_ceiling guard
    haiku_projected: float
    opus_projected: float
    total_projected: float

    @property
    def haiku_trials(self) -> int:
        return self.haiku_subset * self.reps * 2  # 2 arms

    @property
    def opus_trials(self) -> int:
        return self.opus_slice * 2  # 2 arms, 1 rep

    def table(self) -> str:
        """A projected-cost table for the chosen ceiling (printed by author_pilot)."""
        rows = [
            f"pilot design under ceiling ${self.ceiling:.2f}  (ESTIMATES — the pilot's own metering replaces them)",
            f"  per-trial cost estimate:  opus ${self.cost_opus_trial:.4f}   haiku ${self.cost_haiku_trial:.4f}",
            "  experiment          arms  tasks  reps  trials   projected$",
            "  ------------------  ----  -----  ----  ------  -----------",
            f"  calibration-haiku      2  {self.haiku_subset:5d}  {self.reps:4d}  {self.haiku_trials:6d}  "
            f"{self.haiku_projected:11.2f}",
            f"  opus-cost-slice        2  {self.opus_slice:5d}     1  {self.opus_trials:6d}  "
            f"{self.opus_projected:11.2f}",
            "  ------------------  ----  -----  ----  ------  -----------",
            f"  TOTAL                                        {self.haiku_trials + self.opus_trials:6d}  "
            f"{self.total_projected:11.2f}     (<= ${self.ceiling:.2f} ceiling)",
        ]
        return "\n".join(rows)


def _haiku_cost(subset: int, reps: int, ch: float) -> float:
    return round(subset * reps * 2 * ch, 4)


def _opus_cost(opus_slice: int, co: float) -> float:
    return round(opus_slice * 2 * co, 4)


def plan_pilot(ceiling: float, *, n_corpus: int, cost_haiku_trial: float,
               cost_opus_trial: float) -> PilotDesign:
    """Choose the largest pilot design whose projected spend fits ``ceiling``.

    Deterministic greedy growth in a fixed, documented priority order that
    improves the CalibrationVariance the MDE gate consumes:
      1. grow the haiku subset toward the whole corpus (breadth + a better p);
      2. bump reps 1→2 (the correlated-rep clustered-variance signal);
      3. grow the opus slice toward the cap (cost-sample breadth across classes).
    Refuses LOUDLY when even the minimal design overruns the ceiling."""
    ch, co = cost_haiku_trial, cost_opus_trial
    max_subset = min(n_corpus, n_corpus)  # the whole corpus
    opus_cap = min(OPUS_SLICE_CAP, n_corpus)
    if MIN_HAIKU_SUBSET > n_corpus or MIN_OPUS_SLICE > n_corpus:
        raise CeilingTooLowError(
            f"corpus of {n_corpus} tasks is too small for the minimal pilot "
            f"(needs >= {max(MIN_HAIKU_SUBSET, MIN_OPUS_SLICE)} tasks)")

    def total(subset: int, reps: int, opus_slice: int) -> float:
        return round(_haiku_cost(subset, reps, ch) + _opus_cost(opus_slice, co), 4)

    min_cost = total(MIN_HAIKU_SUBSET, 1, MIN_OPUS_SLICE)
    if min_cost > ceiling:
        raise CeilingTooLowError(
            f"minimal pilot (haiku subset {MIN_HAIKU_SUBSET} x1 rep + opus slice "
            f"{MIN_OPUS_SLICE}) projects ${min_cost:.2f} > ceiling ${ceiling:.2f} at "
            f"opus ${co:.4f}/trial, haiku ${ch:.4f}/trial. Raise --ceiling (the real "
            "measured costs may be lower — these are conservative estimates), or reduce "
            "the estimated token profile if you have grounds to.")

    subset, reps, opus_slice = MIN_HAIKU_SUBSET, 1, MIN_OPUS_SLICE
    while subset < max_subset and total(subset + 1, reps, opus_slice) <= ceiling:
        subset += 1
    if reps < MAX_REPS and total(subset, reps + 1, opus_slice) <= ceiling:
        reps += 1
    while opus_slice < opus_cap and total(subset, reps, opus_slice + 1) <= ceiling:
        opus_slice += 1

    return PilotDesign(
        haiku_subset=subset, reps=reps, opus_slice=opus_slice,
        cost_haiku_trial=ch, cost_opus_trial=co, ceiling=ceiling,
        haiku_projected=_haiku_cost(subset, reps, ch),
        opus_projected=_opus_cost(opus_slice, co),
        total_projected=total(subset, reps, opus_slice),
    )


# --------------------------------------------------------------------------- #
# The D4 spend projection (flagship).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DesignProjection:
    name: str
    arms: int
    trials: int
    solve_cost: float
    judge_cost: float

    @property
    def total(self) -> float:
        return round(self.solve_cost + self.judge_cost, 4)


@dataclass(frozen=True)
class FlagshipProjection:
    two_by_two: DesignProjection
    staged: DesignProjection
    flagship_ceiling: float
    cost_per_judgment: float
    chosen: str  # "2x2" | "staged"
    designs: dict = field(default_factory=dict)


def project_flagship(*, n_tasks: int, reps: int, cost_haiku_trial: float,
                     cost_opus_trial: float, flagship_ceiling: float,
                     cost_per_judgment: float = 0.0) -> FlagshipProjection:
    """Project total spend for the 2×2 and the staged haiku-first design, then
    apply the D4 rule against ``flagship_ceiling``.

    * **2×2** = {opus,haiku} × {bare,grounded} — 2 opus arms + 2 haiku arms.
    * **staged** = haiku bare-vs-grounded (the first official run of the staged
      alternative) — 2 haiku arms.
    ``solve_cost`` is the per-trial (arm) spend, the dominant term (plan §6: the
    two Opus arms dominate). ``judge_cost`` is an OPTIONAL add-on (default 0 —
    advisory + idempotent, run once) computed as ``trials x cost_per_judgment``;
    the pilot skips the judge, so a measured per-judgment cost only exists once the
    flagship judge has run. The D4 decision keys on ``total``."""
    trials_2x2 = n_tasks * reps * 4
    solve_2x2 = round(n_tasks * reps * (2 * cost_opus_trial + 2 * cost_haiku_trial), 4)
    two = DesignProjection("2x2 (holm)", 4, trials_2x2, solve_2x2,
                           round(trials_2x2 * cost_per_judgment, 4))

    trials_staged = n_tasks * reps * 2
    solve_staged = round(n_tasks * reps * (2 * cost_haiku_trial), 4)
    staged = DesignProjection("staged haiku-first", 2, trials_staged, solve_staged,
                              round(trials_staged * cost_per_judgment, 4))

    chosen = "2x2" if two.total <= flagship_ceiling else "staged"
    return FlagshipProjection(
        two_by_two=two, staged=staged, flagship_ceiling=flagship_ceiling,
        cost_per_judgment=cost_per_judgment, chosen=chosen,
        designs={"2x2": two, "staged": staged},
    )


def decide_d4(proj: FlagshipProjection) -> str:
    """The D4 rule, as a pure predicate over a projection: the locked 2×2 IFF its
    projected total spend is within the flagship ceiling; otherwise staged
    haiku-first as the first official run."""
    return proj.chosen


def d4_table(proj: FlagshipProjection) -> str:
    """The human-facing D4 decision table (printed by author_flagship)."""
    lines = [
        f"D4 DECISION TABLE   (flagship-ceiling = ${proj.flagship_ceiling:.2f}; "
        f"judge add-on ${proj.cost_per_judgment:.4f}/judgment)",
        "  rule: choose the locked 2x2 IFF projected_total(2x2) <= flagship-ceiling; else staged haiku-first.",
        "  design               arms  trials   solve$    judge$      total$   <= ceiling?",
        "  -------------------  ----  ------  --------  --------  ----------  -----------",
    ]
    for d in (proj.two_by_two, proj.staged):
        fits = "yes" if d.total <= proj.flagship_ceiling else "NO"
        star = "  <== CHOSEN" if (
            (proj.chosen == "2x2" and d is proj.two_by_two)
            or (proj.chosen == "staged" and d is proj.staged)) else ""
        lines.append(
            f"  {d.name:19s}  {d.arms:4d}  {d.trials:6d}  {d.solve_cost:8.2f}  "
            f"{d.judge_cost:8.2f}  {d.total:10.2f}  {fits:>11s}{star}")
    lines.append(f"  CHOSEN: {proj.chosen}")
    return "\n".join(lines)
