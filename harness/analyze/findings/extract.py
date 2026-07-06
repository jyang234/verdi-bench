"""Findings extraction — the ledger→series + document-computation layer [refactor 07 §1].

The pure functions ``dossier``/``selfcheck``/``card`` already imported from
``report`` privately: the per-task/per-arm value series each metric yields, the
quarantine/orphan/tier/override diagnostics, the judge-preference and secondary
aggregates, and the ``MetricDef`` registry [refactor 07 §2] that keys them. All
on :class:`~harness.ledger.view.LedgerView`. ``compute_findings`` is the pure
core — a reproducible function of ``(ledger, spec, seed, corpus_manifest)`` that
assembles these series into a :class:`~harness.analyze.findings.model.FindingsDocument`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from ...contamination.summary import contamination_summary
from ...ledger import events
from ...ledger.query import ledger_head_hash, verify
from ...ledger.view import LedgerView
from ...run.control_reuse import primary_pair_contender
from ...schema.metrics import PrimaryMetric
from ...version import instrument_identity
from ..confounds import asymmetric_null_fields, flag_confounds
from ..effect import effect_sizes
from ..nullsim import NULL_BINARY, NULL_CONTINUOUS, coverage_from_deltas
from ..stats import DEFAULT_CI_LEVEL, BootstrapResult, paired_bootstrap
from .model import (
    AnalyzeError,
    ComparisonFinding,
    FindingsDocument,
    MDEBlock,
    Provenance,
)


# Raw token fields are never compared across vendors [EVAL-6 constraint].
_RAW_TOKEN_FIELDS = ("tokens_in", "tokens_out", "tokens_cache")
# Cross-vendor comparisons are restricted to these dimensions.
_CROSS_VENDOR_ALLOWED = ("cost", "wall_time_s", "tool_calls")


# --- metric extraction -----------------------------------------------------
def _trial_index(ledger_path) -> dict[str, dict]:
    """``trial_id -> {task_id, arm}`` from trial records."""
    out = {}
    for ev in LedgerView(ledger_path).by_kind(events.TRIAL):
        rec = ev["trial_record"]
        out[rec["trial_id"]] = rec
    return out


def _holdout_values(ledger_path) -> dict[str, dict[str, list[float]]]:
    """``task_id -> arm -> [binary pass (0/1) per trial]`` from grade events."""
    trials = _trial_index(ledger_path)
    quarantined = _quarantined_trial_ids(ledger_path)
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in LedgerView(ledger_path).by_kind(events.GRADE):
        if ev["trial_id"] in quarantined:
            continue
        rec = trials.get(ev["trial_id"])
        if rec is None:
            continue
        acc[rec["task_id"]][rec["arm"]].append(1.0 if ev["binary_score"] else 0.0)
    return acc


# --- EVAL-11: operator quarantine [D003, D007] -------------------------------
def _quarantine_entries(ledger_path) -> list[dict]:
    """Ledgered operator quarantines, with the acting operator — the only path
    by which forensics affects a comparison, always a disclosed human act."""
    return [
        {
            "trial_id": ev["forensic_quarantine"]["trial_id"],
            "reason": ev["forensic_quarantine"]["reason"],
            "actor": ev["provenance"]["actor"],
        }
        for ev in LedgerView(ledger_path).by_kind(events.FORENSIC_QUARANTINE)
    ]


def _quarantined_trial_ids(ledger_path) -> set[str]:
    return {e["trial_id"] for e in _quarantine_entries(ledger_path)}


def _quarantined_comparison_ids(ledger_path) -> set[str]:
    """The judged comparisons a quarantined trial participated in — a verdict
    over a quarantined response leaves the comparison with its trial [D007]."""
    from ...judge.assemble import comparison_id_for

    quarantined = _quarantined_trial_ids(ledger_path)
    if not quarantined:
        return set()  # the common case: skip the trial-index rebuild entirely
    trials = _trial_index(ledger_path)
    out: set[str] = set()
    for trial_id in quarantined:
        rec = trials.get(trial_id)
        if rec is not None:
            out.add(comparison_id_for(rec["task_id"], rec["repetition"]))
    return out


def _orphan_grades(ledger_path) -> list[str]:
    """Grade events whose ``trial_id`` has no matching trial record [AN-9].

    A grade with no trial is a ledger inconsistency that silently shrinks n; it is
    surfaced on the findings and rendered loudly, never dropped in silence."""
    trials = _trial_index(ledger_path)
    return sorted(
        ev["trial_id"]
        for ev in LedgerView(ledger_path).by_kind(events.GRADE)
        if ev["trial_id"] not in trials
    )


def _ledger_consistency(ledger_path) -> dict:
    """Ledger-consistency diagnostics that ride every render [AN-9]."""
    orphans = _orphan_grades(ledger_path)
    return {"orphan_grades": orphans, "n_orphan_grades": len(orphans)}


def _tier_summary(ledger_path) -> dict:
    """Grade-trust tiers across the experiment's trials [AN-11, AC-9].

    Local / fake-engine results are ADVISORY, not trusted-container grades; the
    tier is surfaced in the render so "Local = ADVISORY" is reflected, not just
    silently stamped on each record."""
    from ...adapters.base import ADVISORY

    view = LedgerView(ledger_path)
    tier_set = {
        # `... or {}` / `... or ADVISORY` (not `.get(default)`): a record whose
        # provenance or tier serialized as JSON null must still read as the
        # lowest-trust ADVISORY band, never crash sorted() on a None member.
        (ev["trial_record"].get("provenance") or {}).get("tier") or ADVISORY
        for ev in view.by_kind(events.TRIAL)
    }
    # 7B-3: the grade-level `grader` stamp is authoritative for grade trust, not
    # only the trial's provenance tier. An explicit `--runner local` grade over
    # trusted-tier trials (the write-only-stamp hole) must still banner ADVISORY.
    # A grader field present and ≠ "docker" (i.e. "local" or "unknown") is
    # advisory; an absent field (pre-stamp ledger) adds no new signal.
    for ev in view.by_kind(events.GRADE):
        grader = ev.get("grader")
        if grader is not None and grader != "docker":
            tier_set.add(ADVISORY)
    tiers = sorted(tier_set)
    return {"tiers": tiers, "advisory": ADVISORY in tiers}


def _override_summary(ledger_path) -> dict:
    """Terminal-override disclosure [D-P7-2].

    Counts grade-family events (``grade`` / ``cant_grade``) carrying
    ``override_of`` — the trials whose terminal ``cant_grade`` was re-attempted
    via ``bench grade --retry-terminal``. The count is disclosed in both renders
    so a manual override is never invisible in the findings."""
    trials: set[str] = set()
    n_events = 0
    view = LedgerView(ledger_path)
    for kind in (events.GRADE, events.CANT_GRADE):
        for ev in view.by_kind(kind):
            if "override_of" in ev:
                trials.add(ev["trial_id"])
                n_events += 1
    return {"n_override_events": n_events, "override_trials": sorted(trials)}


def _telemetry_values(ledger_path, field: str) -> dict[str, dict[str, list[float]]]:
    """``task_id -> arm -> [telemetry field per non-null trial]``."""
    quarantined = _quarantined_trial_ids(ledger_path)
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in LedgerView(ledger_path).by_kind(events.TRIAL):
        rec = ev["trial_record"]
        if rec["trial_id"] in quarantined:
            continue
        val = rec.get("telemetry", {}).get(field)
        if val is not None:
            acc[rec["task_id"]][rec["arm"]].append(float(val))
    return acc


# --- the primary-metric registry [refactor 07 §2] --------------------------
# ``task_id -> arm -> [per-trial value]`` — the per-task per-arm value series a
# metric's extraction yields (None for a pairwise-only metric that has no
# per-arm series).
PerTaskSeries = dict[str, dict[str, list[float]]]


@dataclass(frozen=True)
class MetricDef:
    """One primary metric's analyze-time behavior, keyed by its ``PrimaryMetric``
    enum value [refactor 07 §2].

    Collapses the string-dispatch that was scattered across extraction, claim
    tag, null model, and reuse onto one record so a new metric is an enum value
    + one entry + a power-sim entry, never a hunt across if/elif chains:

    * ``telemetry_field`` — the trial-telemetry key a continuous metric reads
      (``None`` for holdout-pass-rate and judge-preference);
    * ``claim_tag`` [AN-6] — ``computed`` (a deterministic function of the
      ledger) vs ``judgment`` (the advisory judge's preference);
    * ``null_model`` [AN-4] — the coverage null appropriate to the metric;
      cost/wall-time are continuous, holdout/judge are bounded, and a continuous
      primary must not be scored under a binary null;
    * ``pairwise_only`` — judge-preference has no per-arm absolute (its series is
      a per-pair win-rate built from verdicts), so an absolute would be a
      fabrication;
    * ``extract`` — ``ledger_path -> PerTaskSeries``; ``None`` for a
      pairwise-only metric whose series is derived per pair in
      :func:`_comparison_series`.
    """

    id: str
    telemetry_field: Optional[str]
    claim_tag: str
    null_model: str
    pairwise_only: bool
    extract: Callable[..., Optional[PerTaskSeries]]


METRICS: dict[str, MetricDef] = {
    PrimaryMetric.holdout_pass_rate.value: MetricDef(
        id=PrimaryMetric.holdout_pass_rate.value,
        telemetry_field=None,
        claim_tag="computed",
        null_model=NULL_BINARY,
        pairwise_only=False,
        extract=lambda ledger_path: _holdout_values(ledger_path),
    ),
    PrimaryMetric.cost_per_task.value: MetricDef(
        id=PrimaryMetric.cost_per_task.value,
        telemetry_field="cost",
        claim_tag="computed",
        null_model=NULL_CONTINUOUS,
        pairwise_only=False,
        extract=lambda ledger_path: _telemetry_values(ledger_path, "cost"),
    ),
    PrimaryMetric.wall_time.value: MetricDef(
        id=PrimaryMetric.wall_time.value,
        telemetry_field="wall_time_s",
        claim_tag="computed",
        null_model=NULL_CONTINUOUS,
        pairwise_only=False,
        extract=lambda ledger_path: _telemetry_values(ledger_path, "wall_time_s"),
    ),
    PrimaryMetric.judge_preference.value: MetricDef(
        id=PrimaryMetric.judge_preference.value,
        telemetry_field=None,
        claim_tag="judgment",
        null_model=NULL_BINARY,
        pairwise_only=True,
        # pairwise-only: the per-pair win-rate series is built by
        # _judge_preference_by_task inside _comparison_series, not here.
        extract=lambda ledger_path: None,
    ),
}


def metric_def(primary: str) -> MetricDef:
    """The :class:`MetricDef` for ``primary`` — or a loud failure [refactor 07 §2].

    A missing metric is never silently skipped: an unregistered primary raises
    an :class:`AnalyzeError` naming it, so a new ``PrimaryMetric`` value without a
    registry entry fails at the first dispatch rather than rendering an empty
    section. The REGISTRY-keys == enum-values meta-test catches the drift up
    front; this is the fail-closed backstop at runtime."""
    try:
        return METRICS[primary]
    except KeyError:
        raise AnalyzeError(
            f"no MetricDef registered for primary metric {primary!r}; "
            f"registered: {sorted(METRICS)} [refactor 07 §2]"
        ) from None


def _judge_preference_by_task(
    ledger_path, arm_a: str, arm_b: str
) -> tuple[list[float], list[float]]:
    """Per-task ``(arm_a win-rate, arm_b win-rate)`` for the ``(arm_a, arm_b)``
    comparison [AN-1].

    Each ``judge_verdict`` is attributed to a physical arm through its recorded
    ``arm_map`` (A/B → arm), so a verdict counts **only** for the arm pair it was
    actually judged over — the same pooled verdicts no longer feed every pair, and
    the A↔arm mapping is read, never assumed (a 3-arm design's unjudged pair gets
    no data instead of another pair's verdicts). ``TIE`` and ``CANT_JUDGE`` are
    non-answers: excluded, **never imputed** as 0. Verdicts are grouped by
    ``task_id`` and reduced to a per-task win-rate, so the analysis unit is the
    task cluster (the bootstrap resamples tasks, not individual verdicts). A task
    with no real A/B verdict for this pair contributes nothing.
    """
    rates = _judge_preference_rates(ledger_path, arm_a, arm_b)
    a_vals = [rates[task_id] for task_id in sorted(rates)]
    return a_vals, [1.0 - a for a in a_vals]


def _judge_preference_rates(ledger_path, arm_a: str, arm_b: str) -> dict[str, float]:
    """``task_id -> arm_a win-rate`` for the ``(arm_a, arm_b)`` pair — the
    task-keyed core of :func:`_judge_preference_by_task`, exposed so the
    dossier's per-task view keeps task identity [EVAL-12 AC-6]."""
    pair = {arm_a, arm_b}
    quarantined_cids = _quarantined_comparison_ids(ledger_path)
    per_task: dict[str, dict[str, int]] = defaultdict(lambda: {"a": 0, "n": 0})
    for ev in LedgerView(ledger_path).by_kind(events.JUDGE_VERDICT):
        v = ev["verdict"]
        if v.get("comparison_id") in quarantined_cids:
            continue  # a verdict over a quarantined response leaves with it [D007]
        arm_map = v.get("arm_map")
        if not arm_map or {arm_map.get("A"), arm_map.get("B")} != pair:
            continue  # unmapped, or a different arm pair — never assume the frame
        w = v["winner"]
        if w == "A":
            winner_arm = arm_map["A"]
        elif w == "B":
            winner_arm = arm_map["B"]
        else:
            continue  # TIE / CANT_JUDGE — excluded, never imputed [AN-1]
        task_id = v.get("task_id")
        if task_id is None:
            continue  # cannot cluster an unkeyed verdict
        per_task[task_id]["n"] += 1
        if winner_arm == arm_a:
            per_task[task_id]["a"] += 1
    return {
        task_id: per_task[task_id]["a"] / per_task[task_id]["n"]  # n > 0 by construction
        for task_id in sorted(per_task)
    }


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _paired_arm_series(
    per_task: dict[str, dict[str, list[float]]], arm_a: str, arm_b: str
) -> tuple[list[float], list[float]]:
    """Reduce over reps and pair on tasks present in *both* arms (sorted)."""
    a_vals, b_vals = [], []
    for task_id in sorted(per_task):
        arms = per_task[task_id]
        if arm_a in arms and arm_b in arms and arms[arm_a] and arms[arm_b]:
            a_vals.append(_mean(arms[arm_a]))
            b_vals.append(_mean(arms[arm_b]))
    return a_vals, b_vals


def paired_task_rows(ledger_path, primary: str, arm_a: str, arm_b: str) -> list[dict]:
    """Per-task side-by-side values for one arm pair [EVAL-12 AC-6].

    ``[{task_id, a, b, delta}]`` sorted by task id — the same pairing rules as
    :func:`_comparison_series` (rep-mean reduction, both-arms-present tasks
    only; TIE/CANT_JUDGE excluded for judge_preference), with task identity
    kept so the dossier's analyst layer can render the A-vs-B table. A task a
    metric cannot pair contributes no row — never an imputed zero [D004].
    """
    rows: list[dict] = []
    mdef = metric_def(primary)
    if mdef.pairwise_only:
        rates = _judge_preference_rates(ledger_path, arm_a, arm_b)
        for task_id in sorted(rates):
            a = rates[task_id]
            rows.append({"task_id": task_id, "a": a, "b": 1.0 - a, "delta": 2 * a - 1.0})
        return rows
    per_task = mdef.extract(ledger_path)
    for task_id in sorted(per_task):
        arms = per_task[task_id]
        if arm_a in arms and arm_b in arms and arms[arm_a] and arms[arm_b]:
            a, b = _mean(arms[arm_a]), _mean(arms[arm_b])
            rows.append({"task_id": task_id, "a": a, "b": b, "delta": a - b})
    return rows


def per_arm_absolute_scores(ledger_path, primary: str, spec) -> dict:
    """Per-arm absolute primary-metric score (mean over the arm's per-task
    series) + task count — the 'leaderboard number' for the result card.

    A pure function of the ledger that computes no new inferential statistic
    beyond the per-arm mean. ``judge_preference`` is inherently pairwise (there is
    no per-arm absolute), so its score is ``None`` — never faked into an absolute.
    """
    arm_names = [a.name for a in spec.arms]
    out = {a: {"score": None, "n": 0} for a in arm_names}
    mdef = metric_def(primary)
    if mdef.pairwise_only:
        return out  # pairwise-only; an absolute would be a fabrication
    per_task = mdef.extract(ledger_path)
    series: dict[str, list[float]] = {a: [] for a in arm_names}
    for task_id in sorted(per_task):
        arms = per_task[task_id]
        for a in arm_names:
            if a in arms and arms[a]:
                series[a].append(_mean(arms[a]))
    for a in arm_names:
        vals = series[a]
        out[a] = {"score": (_mean(vals) if vals else None), "n": len(vals)}
    return out


def _comparison_series(
    primary: str, per_task, ledger_path, arm_a: str, arm_b: str
) -> tuple[list[float], list[float], list[float]]:
    """One arm pair's ``(a_vals, b_vals, per-task deltas)`` for the primary metric.

    The single place the per-comparison series is derived, so coverage selection
    (over the primary pair) and each rendered comparison read the same definition.
    """
    if metric_def(primary).pairwise_only:
        a_vals, b_vals = _judge_preference_by_task(ledger_path, arm_a, arm_b)
    else:
        a_vals, b_vals = _paired_arm_series(per_task, arm_a, arm_b)
    deltas = [a - b for a, b in zip(a_vals, b_vals)]
    return a_vals, b_vals, deltas


# --- findings computation --------------------------------------------------
def _lock_event(ledger_path) -> dict:
    locks = LedgerView(ledger_path).by_kind(events.EXPERIMENT_LOCKED)
    if not locks:
        raise AnalyzeError("no experiment_locked event; run `bench plan` first")
    return locks[0]


def _mde_block(ledger_path, realized_n_tasks: Optional[int] = None) -> MDEBlock:
    lock = _lock_event(ledger_path)
    mde = lock.get("mde", {})
    # PL-14: the acknowledgment now rides inline on the lock event. A ledger locked
    # before the fold recorded it as a separate (now-retired) event; still surface
    # it for those legacy ledgers so the acknowledgment is never silently dropped.
    ack = bool(lock.get("acknowledged_underpowered")) or bool(
        LedgerView(ledger_path).by_kind("acknowledged_underpowered")
    )
    value = mde.get("mde")
    plan_n = mde.get("n_tasks")
    achieved = None
    if (
        value is not None
        and plan_n
        and realized_n_tasks
        and realized_n_tasks < plan_n
    ):
        # F-M-S3: MDE scales ~ 1/sqrt(n_clusters) under the paired cluster
        # model — a disclosed first-order approximation, not a re-simulation.
        achieved = round(value * (plan_n / realized_n_tasks) ** 0.5, 4)
    return MDEBlock(
        value=value,
        assumption_based_mde="assumption_based_mde" in mde.get("flags", []),
        acknowledged_underpowered=ack,
        achieved_value=achieved,
        realized_n_tasks=realized_n_tasks,
    )


def _judge_summary(ledger_path) -> dict:
    verdicts = LedgerView(ledger_path).by_kind(events.JUDGE_VERDICT)
    models = sorted({v["verdict"]["provenance"]["judge_model"] for v in verdicts})
    rubrics = sorted({v["verdict"]["provenance"]["rubric_sha256"] for v in verdicts})
    return {"judge_models": models, "rubric_shas": rubrics, "n_verdicts": len(verdicts)}


def _judge_calibration(ledger_path, spec, seed) -> Optional[dict]:
    """Per-class judge↔human kappa + escalation flags [EVAL-2 AC-7, RV-4], through
    the IPW seam at the locked EscalationConfig. None when the judge produced no
    verdicts; the ``by_class`` table is empty until human review exists."""
    verdicts = LedgerView(ledger_path).by_kind(events.JUDGE_VERDICT)
    if not verdicts:
        return None
    from ...review.calibrate import calibration_from_spec

    esc = spec.judge.escalation
    cal = calibration_from_spec(ledger_path, spec, seed)
    # JD-11: surface single-order verdicts so a full experiment that skipped D003
    # order-debiasing cannot do so silently — the flag becomes visible, not just
    # recorded on each verdict.
    single_order = sum(1 for v in verdicts if v["verdict"].get("single_order"))
    return {
        "kappa_threshold": esc.kappa_threshold,
        "min_human_verdicts": esc.min_human_verdicts,
        "single_order_verdicts": single_order,
        "by_class": {
            c: {"kappa": v.kappa, "n": v.n, "sufficient": v.sufficient,
                "escalate": v.escalate, "sensitivity": v.sensitivity,
                "kappa_ci": v.kappa_ci, "n_eff": v.n_eff,
                "inconclusive": v.inconclusive}
            for c, v in sorted(cal.items())
        },
        "escalation_candidates": sorted(c for c, v in cal.items() if v.escalate),
    }


def _integrity(ledger_path) -> dict:
    """Blinding-integrity rate — rides every render [EVAL-7 AC-6].

    Computed from human verdicts' integrity fields; ``None`` rate until human
    review exists, but the field is always present so a render can never omit it.
    """
    recognized, guessed_right, n = 0, 0, 0
    for ev in LedgerView(ledger_path).by_kind(events.HUMAN_VERDICT):
        integrity = ev.get("integrity")
        if integrity is None:
            continue
        n += 1
        if integrity.get("arm_recognized"):
            recognized += 1
            # `is not None` (not truthiness): a valid-but-falsy arm id (e.g. an arm
            # literally named "0") must still count as a guess [RV-6].
            guess = integrity.get("arm_guess")
            if guess is not None and guess == integrity.get("actual_arm"):
                guessed_right += 1
    rate = recognized / n if n else None
    guess_acc = guessed_right / recognized if recognized else None
    return {"rate": rate, "n_reviews": n, "recognized": recognized, "guess_accuracy": guess_acc}


def _secondary_metrics(ledger_path, spec) -> dict:
    """Exploratory per-arm telemetry means, with cross-vendor token honesty.

    Raw token fields are excluded from cross-vendor comparison; when the two arms
    are different vendors, token fields are marked vendor-incomparable [constraint].
    """
    from ..confounds import _vendor

    # EVAL-20 AC-5: an arm's vendor identity is its full declared model set —
    # a mixed-vendor arm (multi-model workflow) makes raw token counts
    # vendor-incomparable for any comparison involving it, and its own token
    # totals are sums over different tokenizers (mixed-tokenizer, named below).
    arm_vendor_sets = {
        a.name: sorted({_vendor(m) for m in a.declared_models()}) for a in spec.arms
    }
    all_vendors = set().union(*arm_vendor_sets.values()) if arm_vendor_sets else set()
    mixed_vendor_arms = sorted(a for a, vs in arm_vendor_sets.items() if len(vs) > 1)
    cross_vendor = len(all_vendors) > 1
    fields = ("tokens_in", "tokens_out", "tokens_cache", "cost", "wall_time_s", "tool_calls")
    per_arm: dict[str, dict[str, float]] = defaultdict(dict)
    raw: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    quarantined = _quarantined_trial_ids(ledger_path)
    # one ledger read serves both the means and the attribution aggregation —
    # the parsed TRIAL projection is passed to _attribution_metrics rather than
    # re-scanned, so a second pass adds no I/O for zero new information
    trial_events = list(LedgerView(ledger_path).by_kind(events.TRIAL))
    for ev in trial_events:
        rec = ev["trial_record"]
        if rec["trial_id"] in quarantined:
            # D007: a quarantined trial's data leaves EVERY rendered aggregate,
            # not just the primary comparison — one document, one exclusion rule
            continue
        for f in fields:
            val = rec.get("telemetry", {}).get(f)
            if val is not None:
                raw[rec["arm"]][f].append(float(val))
    for arm, fvals in raw.items():
        for f, xs in fvals.items():
            per_arm[arm][f] = _mean(xs)
    vendor_incomparable = [f for f in _RAW_TOKEN_FIELDS] if cross_vendor else []
    per_model_means, per_agent_steps = _attribution_metrics(trial_events, quarantined)
    return {
        "exploratory": True,
        "per_arm_means": {a: dict(sorted(v.items())) for a, v in sorted(per_arm.items())},
        "cross_vendor": cross_vendor,
        "vendor_incomparable_fields": vendor_incomparable,
        "cross_vendor_allowed_fields": list(_CROSS_VENDOR_ALLOWED),
        "arm_vendor_sets": arm_vendor_sets,
        "mixed_vendor_arms": mixed_vendor_arms,
        # EVAL-21 AC-5: self-reported attribution (trial-flag testimony), never
        # an official input. Arms absent from these maps reported none —
        # rendered "not attributed", never zero [D004 posture].
        "per_model_means": per_model_means,
        "per_agent_step_counts": per_agent_steps,
    }


def _attribution_metrics(trial_events, quarantined) -> tuple[dict, dict]:
    """Per-arm attribution aggregates [EVAL-21 AC-5], exploratory only.

    Per-model telemetry means come from each trial's ``telemetry_by_model``
    flag (v2 generic logs); per-agent step counts come from *verified*
    trajectories only (``resolve_trajectory`` — an unverifiable artifact is a
    coverage gap, not evidence), with null-agent steps in the explicit
    ``unattributed`` bucket. Trials reporting no attribution contribute
    nothing — absence stays absent, never zero — and an arm whose every step
    is unattributed (single-agent platforms) is dropped here, at the source,
    so a pre-EVAL-21 ledger renders byte-identically.
    """
    from ...run.trajectory import UNATTRIBUTED, resolve_trajectory, slice_by_agent

    model_raw: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    agent_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ev in trial_events:
        rec = ev["trial_record"]
        if rec["trial_id"] in quarantined:
            continue
        arm = rec["arm"]
        for model, block in (rec.get("flags", {}).get("telemetry_by_model") or {}).items():
            for f, val in block.items():
                if val is not None:
                    model_raw[arm][model][f].append(float(val))
        status, traj = resolve_trajectory(
            rec.get("artifacts_path"), ev.get("trajectory_sha")
        )
        if status == "verified" and traj is not None:
            for label, steps in slice_by_agent(traj).items():
                agent_counts[arm][label] += len(steps)
    per_model_means = {
        arm: {
            model: {f: _mean(xs) for f, xs in sorted(fields.items())}
            for model, fields in sorted(models.items())
        }
        for arm, models in sorted(model_raw.items())
    }
    per_agent = {
        arm: dict(sorted(counts.items()))
        for arm, counts in sorted(agent_counts.items())
        if set(counts) != {UNATTRIBUTED}
    }
    return per_model_means, per_agent


def _process_section(ledger_path, spec, seed) -> Optional[dict]:
    """Openly-unblinded process diagnostics [EVAL-9 §M6, PR-5].

    Reads ``process_score`` events into an EXPLORATORY-only section carrying the
    mandatory unblinded disclosure block, plus the per-dimension judge↔human
    kappa and score-vs-telemetry correlations (with ``style_only`` flags) the
    plan's M5 requires [AC-5/AC-7]. Returns None when no process scores exist.
    """
    quarantined = _quarantined_trial_ids(ledger_path)
    evs = [
        ev
        for ev in LedgerView(ledger_path).by_kind(events.PROCESS_SCORE)
        if ev["process_score"]["trial_id"] not in quarantined
    ]
    if not evs:
        return None
    dims: dict[str, dict] = defaultdict(lambda: {"scores": [], "n_cant": 0, "scorer_kinds": set()})
    rubric_versions: set[str] = set()
    scorer_kinds: set[str] = set()
    for ev in evs:
        ps = ev["process_score"]
        rubric_versions.add(ps["rubric_version"])
        kind = ps["provenance"]["scorer"]["kind"]
        scorer_kinds.add(kind)
        for ds in ps["scores"]:
            bucket = dims[ds["dim_id"]]
            bucket["scorer_kinds"].add(kind)
            if ds.get("score") is not None:
                bucket["scores"].append(ds["score"])
            else:
                bucket["n_cant"] += 1
    dimensions = {
        dim: {
            "mean_score": (sum(b["scores"]) / len(b["scores"])) if b["scores"] else None,
            "n_scored": len(b["scores"]),
            "n_cant_score": b["n_cant"],
            "scorer_kinds": sorted(b["scorer_kinds"]),
        }
        for dim, b in sorted(dims.items())
    }
    # PR-5: fold in the AC-5 per-dimension kappa and AC-7 telemetry correlations
    # — under the same quarantine exclusion as the dimension means above, so
    # the section cannot disagree with its own diagnostics [D007].
    from ...process.calibrate import dimension_diagnostics

    diagnostics = dimension_diagnostics(
        ledger_path, spec, seed,
        # P4-RUBRIC option (a): the diagnostics dimensions are the UNION of the
        # ledgered dim_ids the means table above shows (``dims``), not silently
        # the default v1 rubric — so a custom-rubric dimension cannot appear in
        # the means and vanish from the kappa/correlation tables [refactor 06 §7].
        dim_ids=sorted(dims),
        exclude_trials=frozenset(quarantined),
    )
    return {
        "exploratory": True,
        "disclosure": {
            "unblinded": True,
            "note": "Process scores are an openly-unblinded diagnostic tier. They "
            "are NEVER primary metrics and always carry disclosed scorer identity.",
            "scorer_kinds": sorted(scorer_kinds),
        },
        "rubric_versions": sorted(rubric_versions),
        "dimensions": dimensions,
        "kappa_by_dimension": diagnostics["kappa_by_dimension"],
        "correlations": diagnostics["correlations"],
        "style_only": diagnostics["style_only"],
        "floor_prob": diagnostics["floor_prob"],
    }


def _forensics_section(ledger_path, spec) -> Optional[dict]:
    """Forensic disclosure block [EVAL-11 AC-5/AC-6]: the latest report's flags
    and coverage, the LLM↔human per-detector kappa table, and any operator
    quarantines. Disclosure-only — nothing here feeds the fence [D004]; returns
    None when no forensic activity exists on the ledger."""
    from ...forensics.review import spotcheck_kappa

    report_ev = LedgerView(ledger_path).latest(events.FORENSICS_REPORT)
    quarantined = _quarantine_entries(ledger_path)
    if report_ev is None and not quarantined:
        return None
    # a quarantine naming a trial the ledger does not know excluded nothing —
    # surfaced loudly, the AN-9 orphan-grade posture
    trial_ids = set(_trial_index(ledger_path))
    for q in quarantined:
        q["orphan"] = q["trial_id"] not in trial_ids
    section: dict = {"quarantined": quarantined}
    if report_ev is not None:
        fr = report_ev["forensics_report"]
        section.update(
            {
                "vocabulary_version": fr["vocabulary_version"],
                "flags": fr["flags"],
                "coverage": fr["coverage"],
                "spotcheck_kappa": spotcheck_kappa(ledger_path, spec=spec, report=fr),
            }
        )
        if "reviews" in fr:
            reviews = fr["reviews"]
            cant_reasons = sorted(
                r["cant_review_reason"]
                for r in reviews.values()
                if r.get("cant_review_reason") is not None
            )
            section["reviews"] = {
                "n_reviewed": sum(
                    1 for r in reviews.values() if r.get("suspicions") is not None
                ),
                "n_cant_review": len(cant_reasons),
                "cant_review_reasons": cant_reasons,
            }
        else:
            # --no-review is a SKIPPED advisory pass — never rendered as a
            # pass that ran and reviewed nothing [honest reporting]
            section["reviews"] = None
    return section


def _two_sided_bootstrap_p(deltas, seed: int, n_boot: int) -> float:
    """A two-sided bootstrap p-value for H0: mean paired delta = 0 [PRA-M4].

    Null-recenter the per-task deltas to mean zero, resample, and count how often
    the resampled mean is at least as extreme as the observed mean. Add-one
    smoothed so p is never exactly 0. Seeded — reproducible in ``seed``.
    """
    import numpy as np
    from numpy.random import PCG64, Generator

    from ...plan.seeds import sub_seed

    d = np.asarray(list(deltas), dtype=np.float64)
    n = d.shape[0]
    observed = abs(float(d.mean()))
    centered = d - d.mean()
    rng = Generator(PCG64(sub_seed(seed, "holm_p")))
    boot = centered[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
    extreme = int(np.sum(np.abs(boot) >= observed))
    return (extreme + 1) / (n_boot + 1)


# F-H7: no pair with fewer clusters is ever detected=True. A single task
# cluster yields a zero-width bootstrap CI that trivially "excludes zero" (and
# a Holm p of ~1/(n_boot+1)) — a degenerate resample, not evidence. Two is the
# same threshold nullsim/selfcheck already treat as the insufficiency floor.
MIN_DETECTION_CLUSTERS = 2


def _apply_holm(comparisons, deltas_by_pair, parsed_rule, seed: int, *, n_boot: int, alpha: float) -> None:
    """Holm-Bonferroni step-down across the computable pairwise comparisons,
    rewriting each pair's decision in place [PRA-M4]. Non-computable pairs
    (excluded / no data) are skipped. Every adjusted pair stays official.
    The minimum-cluster floor binds here too [F-H7]: the selfcheck's <2-cluster
    gate only ever inspects the primary pair, so without it a single-task
    secondary pair is declared an official detected effect at p≈1/(n_boot+1)."""
    idxs = [i for i, d in enumerate(deltas_by_pair) if d]
    pvals = {i: _two_sided_bootstrap_p(deltas_by_pair[i], seed, n_boot) for i in idxs}
    m = len(idxs)
    order = sorted(idxs, key=lambda i: (pvals[i], comparisons[i].label))
    reject: dict = {}
    failed = False
    for rank, i in enumerate(order):
        if failed or pvals[i] > alpha / (m - rank):
            reject[i] = False
            failed = True  # step-down: once one holds, all remaining hold
        else:
            reject[i] = True
    for i in idxs:
        cf = comparisons[i]
        floored = len(deltas_by_pair[i]) < MIN_DETECTION_CLUSTERS
        detected = bool(reject.get(i, False)) and not floored
        observed = cf.decision["observed_delta"]
        cf.decision["detected"] = detected
        cf.decision["decides_positive"] = detected and parsed_rule.decides_positive(observed)
        cf.decision["holm_p"] = pvals[i]
        cf.decision["correction"] = "holm"
        if floored:
            cf.decision["floor"] = "insufficient_clusters"


# --- control-reuse exploratory section [control-reuse plan] ----------------
def _reused_holdout_by_task(ledger_path) -> dict[str, list[float]]:
    """``task_id -> [binary pass]`` for the reused control, from reused_grade
    joined to reused_trial (the reused_* kinds, never the native ones)."""
    view = LedgerView(ledger_path)
    trials = {
        e["trial_record"]["trial_id"]: e["trial_record"]
        for e in view.by_kind(events.REUSED_TRIAL)
    }
    acc: dict[str, list[float]] = defaultdict(list)
    for e in view.by_kind(events.REUSED_GRADE):
        g = e["grade"]
        tr = trials.get(g.get("trial_id"))
        if tr is not None:
            acc[tr["task_id"]].append(1.0 if g.get("binary_score") else 0.0)
    return acc


def _reused_telemetry_by_task(ledger_path, field: str) -> dict[str, list[float]]:
    """``task_id -> [telemetry field]`` for the reused control's trials."""
    acc: dict[str, list[float]] = defaultdict(list)
    for e in LedgerView(ledger_path).by_kind(events.REUSED_TRIAL):
        tr = e["trial_record"]
        val = (tr.get("telemetry") or {}).get(field)
        if val is not None:
            acc[tr["task_id"]].append(float(val))
    return acc


def _reuse_judge_winrate(ledger_path, contender_arm, control_arm) -> Optional[dict]:
    """Contender win-rate over reused_judge_verdict — TIE/CANT excluded, never
    imputed. None when the reused judge never produced a decided verdict."""
    wins_contender = decided = 0
    for ev in LedgerView(ledger_path).by_kind(events.REUSED_JUDGE_VERDICT):
        v = ev["verdict"]
        w = v.get("winner")
        if w not in ("A", "B"):
            continue
        winner_arm = (v.get("arm_map") or {}).get(w)
        if winner_arm not in (contender_arm, control_arm):
            continue  # unmapped or foreign arm — exclude, never count (native parity)
        decided += 1
        if winner_arm == contender_arm:
            wins_contender += 1
    if decided == 0:
        return None
    return {
        "decided": decided,
        "contender_arm": contender_arm,
        "contender_win_rate": wins_contender / decided,
    }


def _reuse_section(ledger_path, spec) -> Optional[dict]:
    """The EXPLORATORY, UNPAIRED reuse section, or None when no control was reused.

    Computes an unpaired estimate (reused control group vs fresh contender group)
    for the computed primary metrics, plus the reused judge win-rate — never a
    paired bootstrap (whose matched-conditions assumption a reused control
    violates), never an official decision. Reads only the reused_* kinds."""
    ctrl_ev = LedgerView(ledger_path).latest(events.CONTROL_REUSED)
    if ctrl_ev is None:
        return None
    control_arm = ctrl_ev["control_arm"]
    primary = spec.primary_metric.value
    contender_arm = primary_pair_contender(spec, control_arm)

    computed = None
    mdef = metric_def(primary)
    if contender_arm is not None and not mdef.pairwise_only:
        contender_all = mdef.extract(ledger_path)
        if mdef.telemetry_field is None:  # holdout_pass_rate — graded, not telemetry
            control_by_task = _reused_holdout_by_task(ledger_path)
        else:  # cost_per_task / wall_time — the reused control's own telemetry
            control_by_task = _reused_telemetry_by_task(ledger_path, mdef.telemetry_field)
        control_means = [_mean(v) for v in control_by_task.values() if v]
        contender_means = [
            _mean(v[contender_arm])
            for v in contender_all.values()
            if contender_arm in v and v[contender_arm]
        ]
        if control_means and contender_means:
            c_mean, t_mean = _mean(control_means), _mean(contender_means)
            computed = {
                "metric": primary,
                "control_mean": c_mean,
                "control_n_tasks": len(control_means),
                "contender_mean": t_mean,
                "contender_n_tasks": len(contender_means),
                "delta_contender_minus_control": t_mean - c_mean,
                "paired": False,
            }

    return {
        "exploratory": True,
        "official_decision": False,
        "control_arm": control_arm,
        "contender_arm": contender_arm,
        "source_experiment_id": ctrl_ev["source_experiment_id"],
        "bundle_sha256": ctrl_ev["bundle_sha256"],
        "fingerprint_digest": (ctrl_ev.get("fingerprint") or {}).get("digest"),
        "computed": computed,
        "judge_preference": _reuse_judge_winrate(ledger_path, contender_arm, control_arm),
        "disclosure": (
            "Reused control: the control arm was NOT freshly interleaved with the "
            f"contender — it was imported from source experiment "
            f"{ctrl_ev['source_experiment_id']} (fingerprint-matched). This estimate "
            "is UNPAIRED and exploratory-only; it can never back an official "
            "decision. Contamination, confound, and judge/human calibration are "
            "not run over the reused arm."
        ),
    }


def compute_findings(
    ledger_path,
    spec,
    seed: int,
    *,
    corpus_manifest=None,
    coverage_n_sim: int = 200,
    n_boot: int = 10_000,
) -> FindingsDocument:
    """Compute the findings document — pure and reproducible in ``seed``.

    The >2-arm decision policy comes from the sha-locked spec
    (``spec.multi_arm_correction`` [F-H7, PRA-M4]): ``"none"`` makes only the
    primary pair official; ``"holm"`` makes every pair official under a
    Holm-Bonferroni-adjusted family. Either way the >2-arm comparison is
    disclosed. It is not a parameter here — an analyze-time knob on an
    official decision procedure is exactly what pre-registration forbids.
    """
    primary = spec.primary_metric.value

    # metric → per-task per-arm value series, via the one registry [refactor 07 §2].
    # A pairwise-only metric (judge_preference) has no per-arm series; its
    # per-pair win-rate is built in _comparison_series.
    mdef = metric_def(primary)
    per_task = mdef.extract(ledger_path)
    metric_field = mdef.telemetry_field

    excluded_fields = set(asymmetric_null_fields(ledger_path))
    parsed_rule = spec.parsed_rule

    arm_a = spec.arms[0].name
    claim_tag = mdef.claim_tag
    null_model = mdef.null_model

    comparisons: list[ComparisonFinding] = []
    deltas_by_pair: list = []  # per-comparison deltas, lockstep with `comparisons` [PRA-M4]
    selection = None  # the primary (first) comparison's coverage selection → ci_selection
    for other in spec.arms[1:]:
        arm_b = other.name
        a_vals, b_vals, deltas = _comparison_series(primary, per_task, ledger_path, arm_a, arm_b)
        # AN-4 + AN-10: select the CI method by coverage under a metric-appropriate
        # null at the REALIZED N — THIS comparison's own per-task deltas recentered
        # to H0 — at the SAME n_boot the deployed interval uses. Selecting
        # per-comparison means a degenerate or differently-distributed pair cannot
        # miscalibrate another pair's interval (a 3-arm design's empty first pair no
        # longer forces `percentile` on a well-powered second pair); the primary
        # (first) pair drives the headline ci_selection. No assumed 0.5/0.3/50
        # parameters, never a binary null under a continuous metric.
        sel = coverage_from_deltas(
            deltas, seed, null_model=null_model, n_sim=coverage_n_sim, n_boot=n_boot,
        )
        if selection is None:
            selection = sel
        ci_method = sel.selected_method

        excluded = metric_field is not None and metric_field in excluded_fields
        if excluded:
            # Asymmetric nulls ⇒ the metric is excluded from official comparison
            # and flagged, never imputed [AC-7] — regardless of any partial data.
            comparisons.append(
                ComparisonFinding(
                    label=f"{arm_a} vs {arm_b}", arm_a=arm_a, arm_b=arm_b,
                    n_tasks=len(deltas), stats={}, effect={}, claim_tag=claim_tag,
                    decision={"rule": parsed_rule.raw, "observed_delta": None,
                              "detected": None, "decides_positive": None},
                    excluded_from_official=True,
                    exclusion_reason=(
                        f"telemetry field {metric_field!r} has asymmetric nulls; "
                        "excluded from official comparison, never imputed [AC-7]"
                    ),
                )
            )
            deltas_by_pair.append(None)
            continue
        if not deltas:
            # no paired data — record an explicit empty finding rather than crash
            comparisons.append(
                ComparisonFinding(
                    label=f"{arm_a} vs {arm_b}", arm_a=arm_a, arm_b=arm_b, n_tasks=0,
                    stats={}, effect={}, claim_tag=claim_tag,
                    decision={"rule": parsed_rule.raw, "observed_delta": None,
                              "detected": None, "decides_positive": None},
                    excluded_from_official=True,
                    exclusion_reason="no paired task data",
                )
            )
            deltas_by_pair.append(None)
            continue

        boot: BootstrapResult = paired_bootstrap(deltas, seed, ci_method, n_boot=n_boot)
        eff = effect_sizes(a_vals, b_vals)
        observed = eff.mean_paired_delta
        # AN-8: a decision is positive only when the effect is DETECTED (the CI
        # excludes 0) and the rule fires — never the raw rule on a null delta. The
        # artifact now matches the render, which already gates on detection.
        # F-H7: below the cluster floor there is no detection at all — the
        # decision names the floor so renders phrase it as structurally
        # insufficient, distinct from a genuine null.
        floored = boot.n_tasks < MIN_DETECTION_CLUSTERS
        detected = boot.excludes_zero() and not floored
        decision = {
            "rule": parsed_rule.raw,
            "observed_delta": observed,
            "detected": detected,
            "decides_positive": detected and parsed_rule.decides_positive(observed),
        }
        if floored:
            decision["floor"] = "insufficient_clusters"
        comparisons.append(
            ComparisonFinding(
                label=f"{arm_a} vs {arm_b}",
                arm_a=arm_a,
                arm_b=arm_b,
                n_tasks=boot.n_tasks,
                stats=boot.as_dict(),
                effect=eff.as_dict(),
                claim_tag=claim_tag,
                decision=decision,
                excluded_from_official=excluded,
                exclusion_reason=(
                    f"telemetry field {metric_field!r} has asymmetric nulls; "
                    "excluded from official comparison, never imputed [AC-7]"
                    if excluded
                    else None
                ),
            )
        )
        deltas_by_pair.append(deltas)

    # PRA-M4: multi-arm decision policy. With >2 arms the loop above produced
    # k-1 pairwise findings, each with its own detected/decides_positive. The spec
    # pre-registers ONE decision_rule, so k-1 simultaneous official 95% decisions
    # would inflate the family-wise error rate. Resolve per REVIEW-D-P8-1.
    multi_arm: dict = {}
    n_pairs = len(comparisons)
    if n_pairs > 1:
        if spec.multi_arm_correction == "holm":
            _apply_holm(comparisons, deltas_by_pair, parsed_rule, seed, n_boot=n_boot,
                        alpha=1.0 - DEFAULT_CI_LEVEL)
            multi_arm = {
                "n_arms": len(spec.arms),
                "correction": "holm",
                "note": (
                    f"{n_pairs} pairwise comparisons against {arm_a}; every pair's "
                    "decision is Holm-Bonferroni-adjusted to control the family-wise "
                    "error rate at the pre-registered level. Decisions use "
                    "Holm-adjusted recentered-bootstrap p-values; displayed intervals "
                    "remain unadjusted per-comparison CIs [F-H6]."
                ),
            }
        else:
            # default: only the primary (first) pair is official.
            for cf in comparisons[1:]:
                cf.official_decision = False
            multi_arm = {
                "n_arms": len(spec.arms),
                "correction": "none",
                "note": (
                    f"{n_pairs} pairwise comparisons against {arm_a}; only the "
                    f"pre-registered primary pair ({comparisons[0].label}) carries a "
                    "decision. The remaining pairs are exploratory (CI/effect shown, "
                    "no decision) — the spec pre-registers exactly one decision rule. "
                    "Pre-register multi_arm_correction: holm in the spec for a "
                    "corrected family [F-H7]."
                ),
            }

    corpus_prov = corpus_manifest.provenance_ref() if corpus_manifest is not None else None
    chain_result = verify(ledger_path)
    ident = instrument_identity()
    provenance = Provenance(
        instrument_version=ident["version"],
        instrument_git_sha=ident["git_sha"],
        corpus=corpus_prov,
        ledger_head_hash=ledger_head_hash(ledger_path),
        chain_ok=chain_result.ok,
        judge=_judge_summary(ledger_path),
    )

    return FindingsDocument(
        experiment_id=LedgerView(ledger_path).by_kind(events.EXPERIMENT_LOCKED)[0]["provenance"][
            "experiment_id"
        ],
        seed=seed,
        primary_metric=primary,
        decision_rule=parsed_rule.raw,
        spec_corpus={"id": spec.corpus.id, "version": spec.corpus.version},
        comparisons=comparisons,
        mde=_mde_block(
            ledger_path,
            realized_n_tasks=(
                comparisons[0].n_tasks if comparisons and comparisons[0].stats else None
            ),
        ),
        ci_selection=selection.as_dict(),
        confounds=flag_confounds(ledger_path, spec),
        secondary_metrics=_secondary_metrics(ledger_path, spec),
        integrity=_integrity(ledger_path),
        ledger_consistency=_ledger_consistency(ledger_path),
        tier=_tier_summary(ledger_path),
        overrides=_override_summary(ledger_path),
        rubric_committed=_lock_event(ledger_path).get("rubric_sha256") is not None,
        contamination=contamination_summary(ledger_path, spec, corpus_manifest),
        judge_coverage=_judge_coverage(ledger_path),
        multi_arm=multi_arm,
        process=_process_section(ledger_path, spec, seed),
        judge_calibration=_judge_calibration(ledger_path, spec, seed),
        forensics=_forensics_section(ledger_path, spec),
        reuse=_reuse_section(ledger_path, spec),
        provenance=provenance,
    )


def _judge_coverage(ledger_path) -> dict:
    """CANT_JUDGE exposure [F-M-J1]: how many comparisons the judge attempted
    and how many are terminally unjudgeable (permanently excluded from
    judge_preference and calibration by the re-run skip), by reason."""
    from ...judge.schema import TRANSIENT_CANT_JUDGE

    cant: dict[str, int] = {}
    # F-M-J2: identity_leak counts per task class — a scrub pattern that is
    # over-broad for one class of tasks (e.g. every Google-API task) shows up
    # as a concentrated leak rate there, so the corpus-wide FP pattern the
    # narrowed corpus guards against stays visible if it ever recurs.
    leak_by_class: dict[str, int] = {}
    total = 0
    for ev in LedgerView(ledger_path).by_kind(events.JUDGE_VERDICT):
        v = ev.get("verdict") or {}
        total += 1
        if v.get("winner") == "CANT_JUDGE":
            reason = v.get("reason") or "unknown"
            cant[reason] = cant.get(reason, 0) + 1
            if reason == "identity_leak":
                cls = v.get("task_class") or "default"
                leak_by_class[cls] = leak_by_class.get(cls, 0) + 1
    if not total:
        return {}
    return {
        "verdicts": total,
        "cant_judge": dict(sorted(cant.items())),
        "terminal_cant_judge": sum(
            n for r, n in cant.items() if r not in TRANSIENT_CANT_JUDGE
        ),
        "identity_leak_by_class": dict(sorted(leak_by_class.items())),
    }
