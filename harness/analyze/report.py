"""Findings computation + the pre-registration fence [EVAL-6 §M4].

``compute_findings`` is the pure core: a reproducible function of
``(ledger, spec, seed, corpus_manifest)`` producing a :class:`FindingsDocument`.
``render_findings`` turns it into an official or exploratory render, and is where
the fence is mechanical:

* **official** renders *only* the pre-registered primary metric + decision rule;
  asking for official on anything unregistered is refused [AC-5], and official is
  refused unless the corpus is ``full-run-validated`` [EVAL-8 AC-2 hook];
* **everything else** carries an EXPLORATORY watermark on every section, with
  secondaries always labeled exploratory [AC-5, D003];
* MDE appears in every render; a null is phrased "no effect ≥ MDE detected"
  [AC-3]; ``acknowledged_underpowered`` is surfaced when ledgered;
* the provenance block is schema-required (a missing field fails validation),
  and the ledger head hash is cross-checked against ``verify_chain`` at render
  time [AC-6];
* cross-stack comparisons run only over telemetry both arms measured — a metric
  with asymmetric nulls is excluded and flagged, never imputed [AC-7]; raw token
  counts never cross vendors [EVAL-6 constraint].
"""

from __future__ import annotations

import html as _html
from collections import defaultdict
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from ..contamination.summary import contamination_summary, latest_probe, probe_asymmetries
from ..ledger import events
from ..ledger.query import find_events, ledger_head_hash, verify
from ..run.trajectory import resolve_trajectory
from ..schema.metrics import PrimaryMetric
from ..version import instrument_identity
from .confounds import asymmetric_null_fields, flag_confounds
from .effect import effect_sizes
from .nullsim import NULL_BINARY, NULL_CONTINUOUS, coverage_from_deltas
from .stats import DEFAULT_CI_LEVEL, BootstrapResult, paired_bootstrap

# Telemetry-derived primary metrics and the field each reads.
_METRIC_TELEMETRY_FIELD = {
    "cost_per_task": "cost",
    "wall_time": "wall_time_s",
}
# Raw token fields are never compared across vendors [EVAL-6 constraint].
_RAW_TOKEN_FIELDS = ("tokens_in", "tokens_out", "tokens_cache")
# Cross-vendor comparisons are restricted to these dimensions.
_CROSS_VENDOR_ALLOWED = ("cost", "wall_time_s", "tool_calls")


class AnalyzeError(RuntimeError):
    """Base for analyze-stage failures."""


class UnregisteredOfficialError(AnalyzeError):
    """Official render requested for a non-pre-registered metric [AC-5]."""


class CalibrationIncompleteError(AnalyzeError):
    """Official render requested before the corpus is full-run-validated."""


class CorpusMismatchError(AnalyzeError):
    """Official render requested against a corpus that is not the pre-registered
    one — a different id/semver, or one missing tasks the experiment ran [AN-2]."""


class RubricMismatchError(AnalyzeError):
    """Official render requested where a verdict's rubric hash disagrees with the
    lock's committed rubric_sha256 — the rubric was swapped after lock [D-P7-6]."""


class SelfcheckRequiredError(AnalyzeError):
    """Official render requested without a passed ledgered selfcheck [EVAL-1-D008]."""


class ProvenanceError(AnalyzeError):
    """A finding is missing provenance, or the head hash no longer verifies."""


class DisclosureError(AnalyzeError):
    """Process scores rendered without the unblinded disclosure block [EVAL-9 AC-2]."""


class AsymmetricContaminationError(AnalyzeError):
    """Official render requested with asymmetric flagged contamination — one
    arm's model flagged on a task another arm is not, so the pairing itself is
    invalid; exploratory still renders, watermarked [EVAL-10 AC-5, D001]."""


class InsulationAlarmError(AnalyzeError):
    """Official render requested while the latest contamination probe carries a
    holdout-leak insulation alarm [F-M-C3, EVAL-4 AC-9] — an insulation
    VIOLATION that must be investigated (and, if intentional, resolved through
    the ledgered quarantine ceremony + re-scan), never rendered past."""


class CorrectionMismatchError(AnalyzeError):
    """Official render whose multi-arm correction differs from a prior official
    render's recorded correction [F-H7] — one experiment, one pre-registered
    decision procedure; a second official procedure is the post-hoc degree of
    freedom the lock exists to prevent."""


class CantAnalyzeReason(str, Enum):
    """Closed set of fail-closed analyze-refusal reasons [AN-3]."""

    calibration_incomplete = "calibration_incomplete"
    corpus_mismatch = "corpus_mismatch"
    unregistered_metric = "unregistered_metric"
    disclosure_missing = "disclosure_missing"
    provenance_invalid = "provenance_invalid"
    rubric_mismatch = "rubric_mismatch"
    selfcheck_required = "selfcheck_required"
    asymmetric_contamination = "asymmetric_contamination"
    insulation_alarm = "insulation_alarm"
    correction_mismatch = "correction_mismatch"
    analyze_error = "analyze_error"


def cant_analyze_reason(exc: AnalyzeError) -> CantAnalyzeReason:
    """Map an ``AnalyzeError`` to its enumerated ``cant_analyze`` reason.

    Every official-fence refusal must carry its own distinguishable reason in
    this closed set [AN-3] — a generic ``analyze_error`` fallback would erase
    which gate refused. The Phase-7 fence checks (rubric-swap, missing/failed
    selfcheck) are mapped here alongside the calibration/corpus/disclosure ones.
    """
    return {
        CalibrationIncompleteError: CantAnalyzeReason.calibration_incomplete,
        CorpusMismatchError: CantAnalyzeReason.corpus_mismatch,
        UnregisteredOfficialError: CantAnalyzeReason.unregistered_metric,
        DisclosureError: CantAnalyzeReason.disclosure_missing,
        ProvenanceError: CantAnalyzeReason.provenance_invalid,
        RubricMismatchError: CantAnalyzeReason.rubric_mismatch,
        SelfcheckRequiredError: CantAnalyzeReason.selfcheck_required,
        AsymmetricContaminationError: CantAnalyzeReason.asymmetric_contamination,
        InsulationAlarmError: CantAnalyzeReason.insulation_alarm,
        CorrectionMismatchError: CantAnalyzeReason.correction_mismatch,
    }.get(type(exc), CantAnalyzeReason.analyze_error)


# --- schema ----------------------------------------------------------------
class Provenance(BaseModel):
    # every field required ⇒ a render missing any provenance fails validation [AC-6]
    model_config = ConfigDict(extra="forbid")
    instrument_version: str
    instrument_git_sha: str
    corpus: Optional[dict]
    ledger_head_hash: str
    chain_ok: bool
    judge: dict


class ComparisonFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    arm_a: str
    arm_b: str
    n_tasks: int
    stats: dict
    effect: dict
    decision: dict
    # AN-6: machine-checkable provenance of the claim — "computed" (a deterministic
    # function of the ledger) vs "judgment" (rests on the advisory judge)
    claim_tag: Literal["computed", "judgment"]
    excluded_from_official: bool = False
    exclusion_reason: Optional[str] = None
    # PRA-M4: in a >2-arm design, only the pre-registered primary pair
    # (arms[0] vs arms[1]) carries an official decision by default; additional
    # pairs render their CI/effect but are exploratory (no decision), because the
    # spec pre-registers exactly one decision_rule. With --multi-arm-correction
    # =holm every pair is official under a Holm-adjusted family. Absent field on a
    # 2-arm finding = the single official pair.
    official_decision: bool = True


class MDEBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Optional[float]
    assumption_based_mde: bool
    acknowledged_underpowered: bool


class FindingsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    seed: int
    primary_metric: str
    decision_rule: str
    # the pre-registered corpus identity, so the official fence can bind a cited
    # manifest to the spec's corpus without re-reading the spec at render [AN-2]
    spec_corpus: dict
    comparisons: list[ComparisonFinding]
    mde: MDEBlock
    ci_selection: dict
    confounds: list[dict]
    secondary_metrics: dict
    integrity: dict
    # AN-9: orphan grades (no matching trial) counted, never silently dropped
    ledger_consistency: dict
    # AN-11: grade-trust tiers — local/fake results are ADVISORY, surfaced not stamped
    tier: dict
    # D-P7-2: terminal-override disclosure — count of --retry-terminal re-attempts
    overrides: dict = {}
    # D-P7-6: whether the lock committed a rubric_sha256; a legacy lock (False)
    # gets a caveat line in the official render instead of a refusal.
    rubric_committed: bool = True
    # EVAL-10 AC-5: per-arm contamination summary (tri-state counts + flagged
    # task ids + asymmetry) — disclosed in BOTH renders, fenced when asymmetric.
    contamination: dict = {}
    # F-M-J1: judge coverage — terminal CANT_JUDGE comparisons are silently
    # excluded from judge_preference and calibration (a biased missing-data
    # channel when exclusions correlate with outcomes, e.g. a canary salted
    # only on losing trials); the counts are disclosed in both renders.
    judge_coverage: dict = {}
    # PRA-M4: multi-arm disclosure — {n_arms, correction, note}. Non-empty and
    # non-optional in the render whenever >2 arms were compared, so k-1
    # simultaneous decisions can never be presented without saying so.
    multi_arm: dict = {}
    process: Optional[dict] = None
    judge_calibration: Optional[dict] = None
    # EVAL-11: forensic flags/coverage/kappa + operator quarantines — additive,
    # disclosure-only (never a fence input, never a primary metric) [D004]
    forensics: Optional[dict] = None
    provenance: Provenance


# --- metric extraction -----------------------------------------------------
def _trial_index(ledger_path) -> dict[str, dict]:
    """``trial_id -> {task_id, arm}`` from trial records."""
    out = {}
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        out[rec["trial_id"]] = rec
    return out


def _holdout_values(ledger_path) -> dict[str, dict[str, list[float]]]:
    """``task_id -> arm -> [binary pass (0/1) per trial]`` from grade events."""
    trials = _trial_index(ledger_path)
    quarantined = _quarantined_trial_ids(ledger_path)
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in find_events(ledger_path, events.GRADE):
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
        for ev in find_events(ledger_path, events.FORENSIC_QUARANTINE)
    ]


def _quarantined_trial_ids(ledger_path) -> set[str]:
    return {e["trial_id"] for e in _quarantine_entries(ledger_path)}


def _quarantined_comparison_ids(ledger_path) -> set[str]:
    """The judged comparisons a quarantined trial participated in — a verdict
    over a quarantined response leaves the comparison with its trial [D007]."""
    from ..judge.assemble import comparison_id_for

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
        for ev in find_events(ledger_path, events.GRADE)
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
    from ..adapters.base import ADVISORY

    tier_set = {
        # `... or {}` / `... or ADVISORY` (not `.get(default)`): a record whose
        # provenance or tier serialized as JSON null must still read as the
        # lowest-trust ADVISORY band, never crash sorted() on a None member.
        (ev["trial_record"].get("provenance") or {}).get("tier") or ADVISORY
        for ev in find_events(ledger_path, events.TRIAL)
    }
    # 7B-3: the grade-level `grader` stamp is authoritative for grade trust, not
    # only the trial's provenance tier. An explicit `--runner local` grade over
    # trusted-tier trials (the write-only-stamp hole) must still banner ADVISORY.
    # A grader field present and ≠ "docker" (i.e. "local" or "unknown") is
    # advisory; an absent field (pre-stamp ledger) adds no new signal.
    for ev in find_events(ledger_path, events.GRADE):
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
    for kind in (events.GRADE, events.CANT_GRADE):
        for ev in find_events(ledger_path, kind):
            if "override_of" in ev:
                trials.add(ev["trial_id"])
                n_events += 1
    return {"n_override_events": n_events, "override_trials": sorted(trials)}


def _telemetry_values(ledger_path, field: str) -> dict[str, dict[str, list[float]]]:
    """``task_id -> arm -> [telemetry field per non-null trial]``."""
    quarantined = _quarantined_trial_ids(ledger_path)
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        if rec["trial_id"] in quarantined:
            continue
        val = rec.get("telemetry", {}).get(field)
        if val is not None:
            acc[rec["task_id"]][rec["arm"]].append(float(val))
    return acc


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
    for ev in find_events(ledger_path, events.JUDGE_VERDICT):
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
    if primary == PrimaryMetric.judge_preference.value:
        rates = _judge_preference_rates(ledger_path, arm_a, arm_b)
        for task_id in sorted(rates):
            a = rates[task_id]
            rows.append({"task_id": task_id, "a": a, "b": 1.0 - a, "delta": 2 * a - 1.0})
        return rows
    if primary == PrimaryMetric.holdout_pass_rate.value:
        per_task = _holdout_values(ledger_path)
    elif primary in _METRIC_TELEMETRY_FIELD:
        per_task = _telemetry_values(ledger_path, _METRIC_TELEMETRY_FIELD[primary])
    else:  # pragma: no cover - enum is closed
        raise AnalyzeError(f"unsupported primary metric {primary!r}")
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
    if primary == PrimaryMetric.judge_preference.value:
        return out  # pairwise-only; an absolute would be a fabrication
    if primary == PrimaryMetric.holdout_pass_rate.value:
        per_task = _holdout_values(ledger_path)
    elif primary in _METRIC_TELEMETRY_FIELD:
        per_task = _telemetry_values(ledger_path, _METRIC_TELEMETRY_FIELD[primary])
    else:  # pragma: no cover - enum is closed
        raise AnalyzeError(f"unsupported primary metric {primary!r}")
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
    if primary == PrimaryMetric.judge_preference.value:
        a_vals, b_vals = _judge_preference_by_task(ledger_path, arm_a, arm_b)
    else:
        a_vals, b_vals = _paired_arm_series(per_task, arm_a, arm_b)
    deltas = [a - b for a, b in zip(a_vals, b_vals)]
    return a_vals, b_vals, deltas


# --- findings computation --------------------------------------------------
def _lock_event(ledger_path) -> dict:
    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    if not locks:
        raise AnalyzeError("no experiment_locked event; run `bench plan` first")
    return locks[0]


def _mde_block(ledger_path) -> MDEBlock:
    lock = _lock_event(ledger_path)
    mde = lock.get("mde", {})
    # PL-14: the acknowledgment now rides inline on the lock event. A ledger locked
    # before the fold recorded it as a separate (now-retired) event; still surface
    # it for those legacy ledgers so the acknowledgment is never silently dropped.
    ack = bool(lock.get("acknowledged_underpowered")) or bool(
        find_events(ledger_path, "acknowledged_underpowered")
    )
    return MDEBlock(
        value=mde.get("mde"),
        assumption_based_mde="assumption_based_mde" in mde.get("flags", []),
        acknowledged_underpowered=ack,
    )


def _claim_tag_for_metric(primary: str) -> str:
    """The claim provenance of the primary metric [AN-6, master plan §6].

    ``computed`` — a deterministic function of the ledger (holdout grading,
    telemetry). ``judgment`` — the advisory judge's preference; the aggregation is
    computed but the underlying signal is a model judgment, and a reader must be
    told which."""
    return "judgment" if primary == PrimaryMetric.judge_preference.value else "computed"


def _null_model_for_metric(primary: str) -> str:
    """The coverage null appropriate to the primary metric [AN-4].

    Cost / wall-time are continuous; holdout-pass-rate and judge-preference are
    bounded (0/1 or ±1) — a continuous primary must not be scored under a binary
    null. The coverage sim resamples the realized deltas either way, so this is a
    disclosure label, but it makes the metric/null match auditable."""
    if primary in _METRIC_TELEMETRY_FIELD:  # cost_per_task, wall_time
        return NULL_CONTINUOUS
    return NULL_BINARY  # holdout_pass_rate, judge_preference


def _judge_summary(ledger_path) -> dict:
    verdicts = find_events(ledger_path, events.JUDGE_VERDICT)
    models = sorted({v["verdict"]["provenance"]["judge_model"] for v in verdicts})
    rubrics = sorted({v["verdict"]["provenance"]["rubric_sha256"] for v in verdicts})
    return {"judge_models": models, "rubric_shas": rubrics, "n_verdicts": len(verdicts)}


def _judge_calibration(ledger_path, spec, seed) -> Optional[dict]:
    """Per-class judge↔human kappa + escalation flags [EVAL-2 AC-7, RV-4], through
    the IPW seam at the locked EscalationConfig. None when the judge produced no
    verdicts; the ``by_class`` table is empty until human review exists."""
    verdicts = find_events(ledger_path, events.JUDGE_VERDICT)
    if not verdicts:
        return None
    from ..review.calibrate import calibration_from_spec

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
    for ev in find_events(ledger_path, events.HUMAN_VERDICT):
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
    from .confounds import _vendor

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
    # find_events re-parses the whole file per call, so a second scan doubles
    # the dominant I/O of findings generation for zero new information
    trial_events = list(find_events(ledger_path, events.TRIAL))
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
    from ..run.trajectory import UNATTRIBUTED, slice_by_agent

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
        for ev in find_events(ledger_path, events.PROCESS_SCORE)
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
    from ..process.calibrate import dimension_diagnostics

    diagnostics = dimension_diagnostics(
        ledger_path, spec, seed, exclude_trials=frozenset(quarantined)
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
    from ..forensics.review import spotcheck_kappa
    from ..ledger.query import latest_event

    report_ev = latest_event(ledger_path, events.FORENSICS_REPORT)
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

    from ..plan.seeds import sub_seed

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

    # metric → per-task per-arm value series
    if primary == PrimaryMetric.holdout_pass_rate.value:
        per_task = _holdout_values(ledger_path)
        metric_field = None
    elif primary in _METRIC_TELEMETRY_FIELD:
        metric_field = _METRIC_TELEMETRY_FIELD[primary]
        per_task = _telemetry_values(ledger_path, metric_field)
    elif primary == PrimaryMetric.judge_preference.value:
        per_task = None
        metric_field = None
    else:  # pragma: no cover - enum is closed
        raise AnalyzeError(f"unsupported primary metric {primary!r}")

    excluded_fields = set(asymmetric_null_fields(ledger_path))
    parsed_rule = spec.parsed_rule

    arm_a = spec.arms[0].name
    claim_tag = _claim_tag_for_metric(primary)
    null_model = _null_model_for_metric(primary)

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
        experiment_id=find_events(ledger_path, events.EXPERIMENT_LOCKED)[0]["provenance"][
            "experiment_id"
        ],
        seed=seed,
        primary_metric=primary,
        decision_rule=parsed_rule.raw,
        spec_corpus={"id": spec.corpus.id, "version": spec.corpus.version},
        comparisons=comparisons,
        mde=_mde_block(ledger_path),
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
        provenance=provenance,
    )


def _judge_coverage(ledger_path) -> dict:
    """CANT_JUDGE exposure [F-M-J1]: how many comparisons the judge attempted
    and how many are terminally unjudgeable (permanently excluded from
    judge_preference and calibration by the re-run skip), by reason."""
    from ..judge.schema import TRANSIENT_CANT_JUDGE

    cant: dict[str, int] = {}
    total = 0
    for ev in find_events(ledger_path, events.JUDGE_VERDICT):
        v = ev.get("verdict") or {}
        total += 1
        if v.get("winner") == "CANT_JUDGE":
            reason = v.get("reason") or "unknown"
            cant[reason] = cant.get(reason, 0) + 1
    if not total:
        return {}
    return {
        "verdicts": total,
        "cant_judge": dict(sorted(cant.items())),
        "terminal_cant_judge": sum(
            n for r, n in cant.items() if r not in TRANSIENT_CANT_JUDGE
        ),
    }


def _judge_coverage_lines(findings: FindingsDocument) -> list[str]:
    """Disclosure lines for terminal CANT_JUDGE exclusions [F-M-J1] — shared by
    the markdown render and the dossier disclosure sections; [] when the judge
    never ran or every comparison was judged."""
    jc = findings.judge_coverage
    if not jc or not jc.get("cant_judge"):
        return []
    detail = ", ".join(f"{r}: {n}" for r, n in jc["cant_judge"].items())
    return [
        f"- {jc['terminal_cant_judge']} of {jc['verdicts']} judged comparison(s) "
        f"terminally unjudgeable ({detail}) — excluded from judge_preference and "
        "calibration, never imputed. If exclusions correlate with outcomes "
        "(e.g. an arm salting canaries on losing trials), judge_preference is "
        "biased by this missing-data channel [F-M-J1]."
    ]


# --- rendering + the fence -------------------------------------------------
def _fmt(x: Optional[float], dp: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{dp}f}"


def _validate_provenance(findings: FindingsDocument) -> None:
    p = findings.provenance
    for name in ("instrument_version", "instrument_git_sha", "ledger_head_hash", "judge"):
        if getattr(p, name) in (None, ""):
            raise ProvenanceError(f"findings provenance missing {name} [AC-6]")


def _validate_process_disclosure(findings: FindingsDocument) -> None:
    """Process scores may never render without the unblinded disclosure [EVAL-9 AC-2]."""
    if findings.process is None:
        return
    disclosure = findings.process.get("disclosure")
    if not disclosure or disclosure.get("unblinded") is not True:
        raise DisclosureError(
            "findings include process scores but no unblinded disclosure block; "
            "process scores never render without disclosure [EVAL-9 AC-2]"
        )


def _assert_head_hash(findings: FindingsDocument, ledger_path) -> None:
    """Cross-check the recorded head hash against verify_chain at render time [AC-6]."""
    result = verify(ledger_path)
    if not result.ok:
        raise ProvenanceError(f"ledger chain does not verify at render: {result.detail}")
    current = ledger_head_hash(ledger_path)
    if current != findings.provenance.ledger_head_hash:
        raise ProvenanceError(
            "ledger head hash changed since the findings were computed "
            f"(recorded {findings.provenance.ledger_head_hash[:12]}…, "
            f"now {current[:12]}…) — findings are stale [AC-6]"
        )


def _multi_arm_lines(findings: FindingsDocument) -> list[str]:
    """PRA-M4: the non-optional >2-arm disclosure — rendered in BOTH modes
    whenever more than one pairwise comparison exists, so k-1 comparisons are
    never presented without saying how their decisions were (or were not) made."""
    ma = findings.multi_arm
    if not ma:
        return []
    return [f"- ⚠ MULTI-ARM ({ma['n_arms']} arms): {ma['note']}", ""]


def _comparison_lines(cf: ComparisonFinding, mde: MDEBlock) -> list[str]:
    lines = [f"**Comparison: {cf.label}**  (n_tasks={cf.n_tasks}) [{cf.claim_tag}]"]
    if not cf.stats:
        lines.append(f"- No paired task data ({cf.exclusion_reason}).")
        return lines
    s = cf.stats
    ci = f"[{_fmt(s['ci_low'])}, {_fmt(s['ci_high'])}]"
    # F-H6: branch on the computed decision — the same field the dossier's
    # verdict layer reads — never a local re-derivation from the raw CI, which
    # diverges from a Holm-rewritten decision and lets one analyze invocation
    # emit two artifacts that disagree.
    detected = bool(cf.decision.get("detected"))
    lines.append(f"- mean paired delta: {_fmt(cf.effect['mean_paired_delta'])}")
    lines.append(f"- Cliff's delta: {_fmt(cf.effect['cliffs_delta'])}")
    # PRA-M14: name the method that ACTUALLY produced the interval; if the
    # configured method fell back (e.g. bca -> percentile on a degenerate input),
    # say so rather than mislabeling a percentile interval as bca.
    method_label = s["ci_method"]
    if s.get("ci_method_fell_back"):
        method_label = f"{s['ci_method']} (fell back from {s['ci_method_requested']})"
    lines.append(
        f"- {int(s['ci_level'] * 100)}% CI ({method_label}, {s['n_boot']} resamples): {ci}"
    )
    mde_val = _fmt(mde.value)
    if not cf.official_decision:
        # PRA-M4: a non-primary pair in a multi-arm design — CI/effect shown, but
        # no decision, because the spec pre-registers exactly one decision rule.
        holm_p = cf.decision.get("holm_p")
        extra = f" (Holm p={_fmt(holm_p, 3)})" if holm_p is not None else ""
        lines.append(
            f"- Exploratory pair (not the pre-registered primary): no decision"
            f"{extra}."
        )
    elif cf.decision.get("floor") == "insufficient_clusters":
        # F-H7: structurally insufficient — distinct from a genuine null, which
        # is a statement about the data, not about there being almost none.
        holm_p = cf.decision.get("holm_p")
        tag = f" (Holm p={_fmt(holm_p, 3)})" if holm_p is not None else ""
        lines.append(
            f"- Insufficient task clusters for any detection "
            f"(n_tasks={cf.n_tasks} < {MIN_DETECTION_CLUSTERS}): no decision "
            f"possible{tag} [F-H7]."
        )
    elif detected:
        decides = cf.decision["decides_positive"]
        holm_p = cf.decision.get("holm_p")
        tag = f" [Holm-adjusted, p={_fmt(holm_p, 3)}]" if holm_p is not None else ""
        lines.append(
            f"- Effect detected. Decision rule `{cf.decision['rule']}` ⇒ "
            f"{'MET' if decides else 'not met'}{tag}."
        )
    else:
        # structural null phrasing [AC-3, D003]
        holm_p = cf.decision.get("holm_p")
        tag = f" [Holm-adjusted, p={_fmt(holm_p, 3)}]" if holm_p is not None else ""
        lines.append(f"- No effect ≥ MDE detected (MDE={mde_val}){tag}.")
    if cf.excluded_from_official:
        lines.append(f"- ⚠ EXCLUDED from official comparison: {cf.exclusion_reason}")
    return lines


def _forensic_flags_for_comparison(findings: FindingsDocument, cf: ComparisonFinding) -> list[str]:
    """Forensic flags on trials of either arm of THIS comparison — rendered
    beside it, non-suppressing [EVAL-11 AC-5]: the comparison's numbers are
    computed exactly as without the flag; the flag is adjacent evidence."""
    if findings.forensics is None:
        return []
    pair = {cf.arm_a, cf.arm_b}
    return [
        f"- ⚠ forensic flag [{f['detector']}]: trial {f['trial_id']} "
        f"(task {f['task_id']}, arm {f['arm']}) — evidence, not a verdict"
        for f in findings.forensics.get("flags", [])
        if f["arm"] in pair
    ]


def _forensics_lines(findings: FindingsDocument) -> list[str]:
    """The forensic disclosure section [EVAL-11 AC-5/AC-6] for both renders."""
    fx = findings.forensics or {}
    lines = [
        "Forensic flags are evidence, never verdicts: no flag alters a grade, "
        "comparison, or fence outcome [EVAL-11 D003/D004]."
    ]
    if "vocabulary_version" in fx:
        lines.append(f"- vocabulary version: {fx['vocabulary_version']}")
        flags = fx.get("flags", [])
        if flags:
            lines += [
                f"- [{f['detector']}] trial {f['trial_id']} (task {f['task_id']}, "
                f"arm {f['arm']})"
                for f in flags
            ]
        else:
            lines.append("- no flags")
        cov = fx["coverage"]
        lines.append(f"- coverage: {cov['covered']}/{cov['trials']} trial(s) profiled")
        # AC-6: each uncovered trial renders its id + reason; full coverage
        # renders no gap line at all
        for gap in cov["gaps"]:
            lines.append(f"  - coverage gap: trial {gap['trial_id']} — {gap['reason']}")
        # EVAL-16 AC-5: where the step-content detectors could look, per arm —
        # and an explicit asymmetry sentence when the arms differ, the
        # telemetry-asymmetry precedent. Old reports simply lack the key.
        detail_cov = cov.get("detail_by_arm") or {}
        if detail_cov:
            for arm in sorted(detail_cov):
                d = detail_cov[arm]
                lines.append(
                    f"- step-content detector coverage [{arm}]: "
                    f"{d['detail_evaluable']}/{d['trials']} trial(s) evaluable "
                    f"({d['steps_with_detail']}/{d['steps_total']} steps carry detail)"
                )
            ratios = {
                arm: (d["detail_evaluable"], d["trials"]) for arm, d in detail_cov.items()
            }
            if len({(n * 1000000) // t if t else 0 for n, t in ratios.values()}) > 1:
                lines.append(
                    "- ASYMMETRIC step-content coverage: the arms were not equally "
                    "inspectable by the transient detectors — a disclosed "
                    "measurement condition, not a correction [EVAL-16 AC-5]"
                )
        if "reviews" in fx:
            rv = fx["reviews"]
            if rv is None:
                lines.append(
                    "- advisory review pass: NOT RUN for this scan (--no-review)"
                )
            else:
                reasons = ", ".join(rv["cant_review_reasons"])
                lines.append(
                    f"- advisory reviews [judgment]: {rv['n_reviewed']} reviewed, "
                    f"{rv['n_cant_review']} CANT_REVIEW"
                    + (f" (reasons: {reasons})" if reasons else "")
                )
        kappa = (fx.get("spotcheck_kappa") or {}).get("kappa_by_detector") or {}
        if kappa:
            lines.append("- LLM↔human agreement (unweighted IPW kappa, per detector):")
            for det, k in kappa.items():
                if not k["sufficient"]:
                    lines.append(f"  - {det}: n={k['n']} (insufficient)")
                else:
                    flag = " ESCALATE" if k["escalate"] else ""
                    lines.append(f"  - {det}: kappa={_fmt(k['kappa'], 3)} (n={k['n']}){flag}")
    for q in fx.get("quarantined", []):
        if q.get("orphan"):
            lines.append(
                f"- ⚠ ORPHAN QUARANTINE by operator {q['actor']}: trial "
                f"{q['trial_id']} — {q['reason']} — NO SUCH TRIAL on this "
                "ledger; nothing was excluded"
            )
        else:
            lines.append(
                f"- ⚠ QUARANTINED by operator {q['actor']}: trial {q['trial_id']} — "
                f"{q['reason']} (excluded from comparisons) [D007]"
            )
    return lines


def _mde_lines(mde: MDEBlock) -> list[str]:
    lines = [f"MDE = {_fmt(mde.value)}"]
    if mde.assumption_based_mde:
        lines.append("  (assumption_based_mde: variance not yet calibrated)")
    if mde.acknowledged_underpowered:
        lines.append("  (acknowledged_underpowered: design ledgered as underpowered)")
    return lines


def _provenance_lines(findings: FindingsDocument) -> list[str]:
    p = findings.provenance
    lines = [
        f"- instrument: {p.instrument_version} @ {p.instrument_git_sha[:12]}",
        f"- ledger head: {p.ledger_head_hash[:16]}…  chain_ok={p.chain_ok}",
        f"- judge: {p.judge}",
        # D002 [computed]: the judge is IDENTITY-blind, not outcome-blind — the
        # packet includes per-response holdout results by design, so
        # judge_preference is not independent of holdout_pass_rate. Disclosed so a
        # reader never mistakes judge agreement for an independent signal.
        "- [computed] judge is identity-blind, not outcome-blind: the packet "
        "includes holdout results by design, so judge_preference is not "
        "independent of holdout_pass_rate [EVAL-2 D002]",
    ]
    if p.corpus is not None:
        lines.append(
            f"- corpus: {p.corpus['corpus_id']}@{p.corpus['semver']} "
            f"({p.corpus['calibration_status']}), {len(p.corpus['task_shas'])} task sha(s)"
        )
    else:
        lines.append("- corpus: (none provided)")
    return lines


def render_markdown(
    findings: FindingsDocument,
    ledger_path,
    mode: Literal["official", "exploratory"] = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Render findings to markdown behind the pre-registration fence."""
    _validate_provenance(findings)
    _validate_process_disclosure(findings)
    _assert_head_hash(findings, ledger_path)

    if mode == "official":
        if metric is not None and metric != findings.primary_metric:
            raise UnregisteredOfficialError(
                f"official render requested for {metric!r}, but the pre-registered "
                f"primary metric is {findings.primary_metric!r}; only the "
                "primary metric + decision rule are official [AC-5]"
            )
        _assert_official_calibration(findings, corpus_manifest, ledger_path)
        return _render_official_md(findings)
    return _render_exploratory_md(findings)


def _task_ids_run(ledger_path) -> set[str]:
    """The set of task ids the experiment actually ran (from trial records)."""
    return {ev["trial_record"]["task_id"] for ev in find_events(ledger_path, events.TRIAL)}


def _ledgered_calibration_status(ledger_path, corpus_id: str, semver: str) -> Optional[str]:
    """The latest calibration status **on the chain** for a corpus, or None.

    Reads ``calibration_run`` events (last-write-wins in ledger order) rather than
    the hand-editable ``manifest.calibration.status`` [CO-4]."""
    status = None
    for ev in find_events(ledger_path, events.CALIBRATION_RUN):
        if ev.get("corpus_id") == corpus_id and ev.get("semver") == semver:
            status = ev.get("status")
    return status


def _assert_official_calibration(findings: FindingsDocument, corpus_manifest, ledger_path) -> None:
    """Bind the official fence to corpus identity + integrity [AN-2, D-P5-2].

    All six checks — a fence that trusts fewer is a hand-editable bypass:

    1. the cited manifest is the **pre-registered** corpus (id + semver match
       ``spec.corpus``); a different corpus cannot be laundered into an official
       finding;
    2. every task the experiment **ran** is an admitted task in that manifest, so
       the manifest actually covers the data;
    3. the corpus is full-run-validated per the **ledgered** ``calibration_run``
       events, not the mutable ``manifest.calibration.status`` [CO-4];
    4. every verdict's rubric hash agrees with the lock's committed
       ``rubric_sha256`` (a post-lock rubric swap is refused) [D-P7-6];
    5. a ledgered ``selfcheck`` with ``passed=true`` exists (the coverage
       self-validation gate) [EVAL-1-D008];
    6. no *asymmetric flagged* contamination — a task flagged for one arm's
       model but not another invalidates the pairing itself; symmetric or
       unknown states are disclosed caveats, not refusals [EVAL-10 AC-5, D001].

    Note on check (2): D-P5-2 framed this as reconciling ``manifest.task_shas()``
    with the lock's ``task_commitment``, but those are different hash domains —
    the commitment hashes each task's *tasks.yaml entry* (corpus/commit.py) while
    ``task_shas()`` are *corpus-cache blob* shas — so a direct sha comparison is
    not well-defined. Membership is the achievable analyze-time binding; the task
    *content* the experiment ran is separately fenced at run/grade/judge time by
    ``assert_task_commitment`` against that same ``task_commitment``. The
    manifest's cited task shas are provenance, not an independently anchored
    integrity claim.
    """
    spec_corpus = findings.spec_corpus
    if corpus_manifest is None:
        raise CalibrationIncompleteError(
            "official findings require a full-run-validated corpus manifest; none "
            f"provided for {spec_corpus['id']}@{spec_corpus['version']} [EVAL-8 AC-2]"
        )
    # 1. corpus identity: the cited manifest must be the pre-registered corpus.
    if (
        corpus_manifest.corpus_id != spec_corpus["id"]
        or corpus_manifest.semver != spec_corpus["version"]
    ):
        raise CorpusMismatchError(
            f"official render cites corpus {corpus_manifest.corpus_id}@"
            f"{corpus_manifest.semver}, but the experiment pre-registered "
            f"{spec_corpus['id']}@{spec_corpus['version']}; the primary metric is "
            "official only against the corpus it was registered on [AN-2]"
        )
    # 2. every task the experiment ran must be admitted in that manifest.
    missing = sorted(t for t in _task_ids_run(ledger_path) if not corpus_manifest.is_schedulable(t))
    if missing:
        raise CorpusMismatchError(
            f"official render cites {corpus_manifest.corpus_id}@{corpus_manifest.semver}, "
            f"but tasks {missing} were run and are not admitted in it; the manifest "
            "does not cover the experiment's data [AN-2]"
        )
    # 3. full-run-validated per the LEDGERED calibration_run events, not manifest JSON.
    status = _ledgered_calibration_status(ledger_path, spec_corpus["id"], spec_corpus["version"])
    if status != "full-run-validated":
        raise CalibrationIncompleteError(
            f"corpus {spec_corpus['id']}@{spec_corpus['version']} is not "
            f"full-run-validated on the chain (ledgered status={status!r}); a "
            "manifest JSON status alone does not satisfy the fence — calibrate "
            "through a ledgered calibration_run before the first official finding "
            "[EVAL-8 AC-2, CO-4]"
        )
    # 4. rubric commitment [D-P7-6]: when the lock committed a rubric_sha256 and
    # verdicts exist, every verdict's provenance rubric hash must equal it — a
    # post-lock rubric swap must not reach an official finding. A legacy lock
    # (no committed hash) is not refused here; the render adds a caveat instead.
    locked_rubric_sha = _lock_event(ledger_path).get("rubric_sha256")
    if locked_rubric_sha is not None:
        verdict_shas = _judge_summary(ledger_path)["rubric_shas"]
        disagreeing = sorted(s for s in verdict_shas if s != locked_rubric_sha)
        if disagreeing:
            raise RubricMismatchError(
                f"official render refused: verdict rubric hash(es) {disagreeing} "
                f"disagree with the locked rubric_sha256 {locked_rubric_sha}; the "
                "judging rubric was swapped after the lock [D-P7-6]"
            )
    # 5. self-validation [EVAL-1-D008]: a ledgered `selfcheck` with passed=true
    # must exist, must not be stale (no data appended after it — review #1), and
    # must have validated the CI method the render deploys (review #2).
    from .selfcheck import latest_selfcheck, selfcheck_status

    status = selfcheck_status(ledger_path)
    if status != "current":
        detail = {
            "missing": "no selfcheck has been run",
            "failed": "the selfcheck failed",
            "stale": "the selfcheck predates later trials/grades — it validated an "
                     "older dataset than this render analyzes",
        }[status]
        raise SelfcheckRequiredError(
            f"official render refused: {detail}. Run `bench selfcheck "
            "<experiment-dir>` and pass it before the first official finding "
            "[EVAL-1-D008]"
        )
    # The selfcheck validated a specific CI method; the render must deploy that
    # same method, else the coverage the gate certified is not the coverage of
    # the interval actually shown [review #2].
    validated = (latest_selfcheck(ledger_path) or {}).get("selected_method")
    deployed = findings.ci_selection.get("selected_method")
    if validated != deployed:
        raise SelfcheckRequiredError(
            f"official render refused: the selfcheck validated CI method "
            f"{validated!r} but the render deploys {deployed!r}; re-run `bench "
            "selfcheck <experiment-dir>` so the validated and deployed methods "
            "agree [EVAL-1-D008]"
        )
    # 6. asymmetric flagged contamination [EVAL-10 AC-5, D001]: the one
    # contamination case A/B cannot disclose its way out of — a flag on one
    # arm's model with the other arm not flagged breaks the pairing. Symmetric
    # flags and unknowns stay disclosed caveats in the render, never refusals.
    # Recomputed from the LEDGERED probe event, like the fence's other checks —
    # the findings field is disclosure, not the thing the fence trusts; the
    # findings-based list still counts (defense in depth for summary-only
    # flags), but an empty findings dict cannot silence a ledgered asymmetry.
    asymmetric = probe_asymmetries(latest_probe(ledger_path)) or (
        findings.contamination or {}
    ).get("asymmetric", [])
    if asymmetric:
        detail = "; ".join(asymmetry_line(a) for a in asymmetric)
        raise AsymmetricContaminationError(
            f"official render refused: asymmetric flagged contamination — "
            f"{detail}. The pairing is invalid for these tasks; exploratory "
            "still renders, watermarked, with the full summary [EVAL-10 AC-5]"
        )
    # 8. holdout-leak insulation alarms [F-M-C3, EVAL-4 AC-9]: an alarm on the
    # latest ledgered probe is an insulation VIOLATION — holdout content
    # reproduced in a solution — and refuses the official render until it is
    # investigated: quarantine the offending trial (ledgered), re-run the scan
    # (quarantined trials are skipped, disclosed) and the probe.
    _assert_no_insulation_alarms(ledger_path)
    # 7. multi-arm correction consistency [F-H7]: one pre-registered decision
    # procedure per experiment. The policy lives in the sha-locked spec, so two
    # official renders cannot legitimately differ through the tool; this is
    # defense in depth against a chain produced under a different policy.
    _assert_correction_consistent(
        (findings.multi_arm or {}).get("correction", "none"), ledger_path
    )


def _assert_no_insulation_alarms(ledger_path) -> None:
    """Refuse an official render while the latest probe carries insulation
    alarms [F-M-C3]. Probes predating the recorded field simply lack it."""
    alarms = (latest_probe(ledger_path) or {}).get("alarms") or []
    if alarms:
        detail = "; ".join(alarms)
        raise InsulationAlarmError(
            f"official render refused: {len(alarms)} holdout-leak insulation "
            f"alarm(s) on the latest contamination probe — {detail}. Investigate; "
            "if intentional, quarantine the trial (ledgered) and re-run "
            "`bench contamination probe` [F-M-C3, EVAL-4 AC-9]"
        )


def _assert_correction_consistent(correction: str, ledger_path) -> None:
    """Refuse an official render whose applied multi-arm correction differs from
    any prior official render's recorded correction [F-H7]. Render events that
    predate the recorded field are skipped — a legacy chain is not refused on,
    but the first post-field official render pins the procedure for the rest."""
    for ev in find_events(ledger_path, events.FINDINGS_RENDERED):
        if ev.get("mode") != "official":
            continue
        prior = ev.get("multi_arm_correction")
        if prior is not None and prior != correction:
            raise CorrectionMismatchError(
                f"official render refused: a prior official render used "
                f"multi-arm correction {prior!r}; this render would use "
                f"{correction!r} — one pre-registered decision procedure per "
                "experiment [F-H7]"
            )


def _override_lines(findings: FindingsDocument) -> list[str]:
    """Disclosure line for terminal-override re-grades [D-P7-2], or [] if none."""
    ov = findings.overrides or {}
    n = ov.get("n_override_events", 0)
    if not n:
        return []
    trials = ov.get("override_trials", [])
    return [
        f"- {n} override-graded re-attempt(s) via `--retry-terminal` on "
        f"{len(trials)} trial(s): {trials}"
    ]


def asymmetry_line(a: dict) -> str:
    """One asymmetric-contamination entry, worded identically in the official
    refusal and the exploratory disclosure so the two accounts reconcile."""
    return (
        f"task {a['task_id']!r} flagged for arm(s) {a['flagged_arms']}, "
        f"not for {a['unflagged_arms']}"
    )


def _contamination_lines(findings: FindingsDocument) -> list[str]:
    """The per-arm contamination disclosure both renders carry [EVAL-10 AC-5].

    Disclosure over suppression: symmetric flags and unknowns are caveat lines
    here; only asymmetry refuses (in the fence, before official rendering)."""
    c = findings.contamination or {}
    if not c:
        return ["- not computed"]
    lines = [f"- probe: {c['probe_status']}"]
    per_arm = c.get("per_arm", {})
    for arm in sorted(per_arm):
        s = per_arm[arm]
        lines.append(
            f"- {arm}: clean_by_date={s['clean_by_date']} unknown={s['unknown']} "
            f"flagged={s['flagged']} flagged_task_ids={s['flagged_task_ids']}"
        )
    flagged_anywhere = any(s["flagged"] for s in per_arm.values())
    if flagged_anywhere and not c.get("asymmetric"):
        lines.append(
            "- ⚠ CAVEAT: symmetric flagged contamination — every flagged task is "
            "flagged for all arms; both arms degrade together, so the finding is "
            "disclosed rather than suppressed [EVAL-10 D001]"
        )
    if any(s["unknown"] for s in per_arm.values()):
        lines.append(
            "- ⚠ CAVEAT: contamination status is unknown for some (task, arm) "
            "pairs (missing created_at/training_cutoff or no probe); unknown is "
            "disclosed as unknown, never upgraded to clean [EVAL-10 AC-1]"
        )
    for a in c.get("asymmetric", []):
        lines.append(f"- ⚠ ASYMMETRIC: {asymmetry_line(a)} — pairing invalid")
    return lines


def _render_official_md(findings: FindingsDocument) -> str:
    out = [
        f"# Official findings — {findings.experiment_id}",
        f"Pre-registered primary metric: **{findings.primary_metric}**",
        f"Decision rule: `{findings.decision_rule}`",
        "",
        "## Minimum detectable effect",
        *_mde_lines(findings.mde),
        "",
        "## Primary metric",
        *_multi_arm_lines(findings),
    ]
    for cf in findings.comparisons:
        if cf.excluded_from_official:
            out.append(
                f"### Comparison: {cf.label} — EXCLUDED ({cf.exclusion_reason})"
            )
            continue
        out.extend(_comparison_lines(cf, findings.mde))
        # EVAL-11 AC-5: flags render beside the comparison they affect
        out.extend(_forensic_flags_for_comparison(findings, cf))
    out += ["", "## Confounds (disclosed, non-suppressing)"]
    out += [f"- {c['flag']}" for c in findings.confounds] or ["- none"]
    out += ["", "## Contamination (disclosed, non-suppressing)"]
    out += _contamination_lines(findings)
    out += ["", f"## Blinding integrity", f"- {_integrity_line(findings)}"]
    tier = _tier_lines(findings)
    if tier:
        out += ["", "## Grade tier", *tier]
    consistency = _ledger_consistency_lines(findings)
    if consistency:
        out += ["", "## Ledger consistency", *consistency]
    override = _override_lines(findings)
    if override:
        out += ["", "## Terminal overrides", *override]
    judge_cov = _judge_coverage_lines(findings)
    if judge_cov:
        out += ["", "## Judge coverage", *judge_cov]
    if not findings.rubric_committed:
        out += [
            "",
            "## Rubric commitment",
            "- ⚠ CAVEAT: this experiment was locked before rubric commitment "
            "(D-P7-6); the judging rubric content is not pinned, so a post-lock "
            "rubric change cannot be detected from the ledger",
        ]
    # AN-12 / REVIEW-D-3: the process section is retained in the official render
    # under an explicit EXPLORATORY/advisory label with the unblinded disclosure —
    # never a primary metric, never stripped (findings.json already hashes it into
    # findings_sha256, so stripping the markdown would desync from the artifact)
    # [EVAL-9 AC-6].
    if findings.process is not None:
        out += [
            "",
            f"## Process diagnostics — {_WATERMARK} (advisory secondary, NEVER a primary metric)",
            *_process_lines(findings),
        ]
    # EVAL-11 AC-5: disclosed in the OFFICIAL render too — disclosure-only,
    # never a fence input [D004]
    if findings.forensics is not None:
        out += [
            "",
            "## Forensic flags (disclosed, non-suppressing)",
            *_forensics_lines(findings),
        ]
    out += ["", "## Provenance", *_provenance_lines(findings)]
    out += ["", f"CI method selected by coverage: {findings.ci_selection['selected_method']}"]
    return "\n".join(out) + "\n"


_WATERMARK = "⚠ EXPLORATORY — not an official, pre-registered finding"


def _render_exploratory_md(findings: FindingsDocument) -> str:
    def section(title: str, body: list[str]) -> list[str]:
        # watermark on EVERY section header [AC-5, D003]
        return [f"## {_WATERMARK}", f"### {title}", *body, ""]

    out = [f"# Findings (EXPLORATORY) — {findings.experiment_id}", _WATERMARK, ""]
    out += section("Pre-registered context", [
        f"- primary metric: {findings.primary_metric}",
        f"- decision rule: `{findings.decision_rule}`",
    ])
    out += section("Minimum detectable effect", _mde_lines(findings.mde))
    if findings.multi_arm:
        out += section("Multi-arm disclosure", _multi_arm_lines(findings))
    for cf in findings.comparisons:
        out += section(
            f"Primary metric — {cf.label}",
            _comparison_lines(cf, findings.mde)
            # EVAL-11 AC-5: flags render beside the comparison they affect
            + _forensic_flags_for_comparison(findings, cf),
        )
    out += section("Secondary metrics (exploratory)", _secondary_lines(findings))
    if findings.judge_calibration is not None:
        out += section("Judge calibration (per class)", _judge_calibration_lines(findings))
    if findings.process is not None:
        out += section("Process diagnostics (EXPLORATORY secondary)", _process_lines(findings))
    if findings.forensics is not None:
        out += section("Forensic flags (disclosed, non-suppressing)", _forensics_lines(findings))
    out += section("Confounds (disclosed, non-suppressing)",
                   [f"- {c['flag']}: {c}" for c in findings.confounds] or ["- none"])
    out += section("Contamination (disclosed, non-suppressing)",
                   _contamination_lines(findings))
    out += section("Blinding integrity", [f"- {_integrity_line(findings)}"])
    tier = _tier_lines(findings)
    if tier:
        out += section("Grade tier", tier)
    consistency = _ledger_consistency_lines(findings)
    if consistency:
        out += section("Ledger consistency", consistency)
    override = _override_lines(findings)
    if override:
        out += section("Terminal overrides", override)
    judge_cov = _judge_coverage_lines(findings)
    if judge_cov:
        out += section("Judge coverage", judge_cov)
    out += section("CI method selection (coverage)", [f"- {findings.ci_selection}"])
    out += section("Provenance", _provenance_lines(findings))
    return "\n".join(out) + "\n"


def _secondary_lines(findings: FindingsDocument) -> list[str]:
    sm = findings.secondary_metrics
    lines = [f"- per-arm means: {sm['per_arm_means']}"]
    if sm["cross_vendor"]:
        lines.append(
            f"- cross-vendor: raw token fields {sm['vendor_incomparable_fields']} are "
            "vendor-incomparable and NOT compared across arms; cross-vendor "
            f"comparisons restricted to {sm['cross_vendor_allowed_fields']}"
        )
    # EVAL-20 AC-5: a mixed-vendor arm is named, and its own token totals are
    # additionally sums over different tokenizers.
    if sm.get("mixed_vendor_arms"):
        lines.append(
            f"- mixed-vendor arm(s) {sm['mixed_vendor_arms']}: declared models span "
            "vendors, so these arms' own token totals are mixed-tokenizer sums "
            f"(vendor sets: {sm['arm_vendor_sets']})"
        )
    # EVAL-21 AC-5: attribution is self-reported testimony (EXPLORATORY, no
    # official gate reads it); an arm that reported none renders "not
    # attributed", never zero. Unattributed-only arms are already dropped at
    # the source (_attribution_metrics), so a pre-EVAL-21 ledger renders
    # byte-identically. The arm listing is the UNION of all sections' arms —
    # an arm with all-null whole-trial telemetry but real attribution must
    # still appear.
    per_model = sm.get("per_model_means") or {}
    per_agent = sm.get("per_agent_step_counts") or {}
    if per_model or per_agent:
        arms = sorted(set(sm["per_arm_means"]) | set(per_model) | set(per_agent))
        lines.append(
            "- per-model/per-agent attribution (self-reported by the arm; "
            "exploratory cross-check only, per-model token counts remain "
            "vendor-bound):"
        )
        for arm in arms:
            models = per_model.get(arm) or "not attributed"
            agents = per_agent.get(arm) or "not attributed"
            lines.append(f"  - {arm}: models={models}; agent step counts={agents}")
    return lines


def _judge_calibration_lines(findings: FindingsDocument) -> list[str]:
    jc = findings.judge_calibration
    lines = [
        f"- thresholds: kappa ≥ {jc['kappa_threshold']} at ≥ {jc['min_human_verdicts']} "
        "EFFECTIVE human verdicts (Kish, IPW-weighted); escalation fires when the "
        "interval's UPPER bound is below threshold — a straddling interval is "
        "INCONCLUSIVE, not silently fine [AC-7, F-M-S4]"
    ]
    if jc.get("single_order_verdicts"):
        lines.append(
            f"- ⚠ {jc['single_order_verdicts']} verdict(s) used single-order judging "
            "(D003 order-debiasing skipped — smoke-run only) [JD-11]"
        )
    if not jc["by_class"]:
        lines.append("- no human-reviewed comparisons yet — kappa pending")
        return lines
    for cls, c in jc["by_class"].items():
        if not c["sufficient"]:
            n_eff = c.get("n_eff")
            eff = f", n_eff={_fmt(n_eff, 1)}" if n_eff is not None else ""
            lines.append(f"- {cls}: n={c['n']}{eff} (insufficient for kappa)")
        else:
            flag = (
                " ESCALATE" if c["escalate"]
                else (" INCONCLUSIVE (interval straddles threshold)"
                      if c.get("inconclusive") else "")
            )
            ci = c.get("kappa_ci")
            ci_txt = f" CI=[{_fmt(ci[0], 3)}, {_fmt(ci[1], 3)}]" if ci else ""
            lines.append(
                f"- {cls}: kappa={_fmt(c['kappa'], 3)}{ci_txt} "
                f"(n={c['n']}, n_eff={_fmt(c.get('n_eff'), 1)}){flag}"
            )
            # D-P7-4: the floor-only sensitivity beside the IPW headline, so the
            # reweighting's leverage on the headline is visible.
            sens = c.get("sensitivity")
            if sens is not None:
                lines.append(f"  - sensitivity (floor-only): kappa={_fmt(sens, 3)}")
    if jc["escalation_candidates"]:
        lines.append(f"- escalation candidates: {jc['escalation_candidates']}")
    return lines


def _process_lines(findings: FindingsDocument) -> list[str]:
    p = findings.process
    disclosure = p["disclosure"]
    lines = [
        "⚠ UNBLINDED DIAGNOSTIC — NOT a primary metric.",
        f"- disclosure: {disclosure['note']}",
        f"- scorers: {disclosure['scorer_kinds']}; rubric(s): {p['rubric_versions']}",
    ]
    for dim, d in p["dimensions"].items():
        lines.append(
            f"- {dim}: mean={_fmt(d['mean_score'], 2)} "
            f"(scored={d['n_scored']}, cant_score={d['n_cant_score']}, "
            f"scorers={d['scorer_kinds']})"
        )
    # PR-5: per-dimension judge↔human kappa [AC-5] and telemetry correlations [AC-7]
    kappa = p.get("kappa_by_dimension") or {}
    if kappa:
        lines.append("- judge↔human agreement (quadratic-weighted IPW kappa):")
        for dim, k in kappa.items():
            if not k["sufficient"]:
                lines.append(f"  - {dim}: n={k['n']} (insufficient)")
            else:
                flag = " ESCALATE" if k["escalate"] else ""
                lines.append(f"  - {dim}: kappa={_fmt(k['kappa'], 3)} (n={k['n']}){flag}")
    corr = p.get("correlations") or {}
    if corr:
        lines.append("- score-vs-telemetry correlation (Spearman):")
        for dim, c in corr.items():
            tag = " [STYLE-ONLY]" if c["style_only"] else ""
            shown = {k: _fmt(v, 2) for k, v in c["correlations"].items()}
            lines.append(f"  - {dim}: {shown}{tag}")
        if p.get("style_only"):
            lines.append(f"- style-only dimensions (uncorrelated with their correlates): {p['style_only']}")
    return lines


def _tier_lines(findings: FindingsDocument) -> list[str]:
    """A loud line when any result is ADVISORY-tier [AN-11]; empty if all trusted."""
    t = findings.tier
    if not t.get("advisory"):
        return []
    return [
        f"- ⚠ ADVISORY: results include ADVISORY-tier grades (local / no trusted "
        f"container) — advisory, not authoritative; tiers present: {t['tiers']} [AC-9]"
    ]


def _ledger_consistency_lines(findings: FindingsDocument) -> list[str]:
    """A loud warning line when orphan grades were excluded [AN-9]; empty if clean."""
    lc = findings.ledger_consistency
    if lc["n_orphan_grades"] == 0:
        return []
    return [
        f"- ⚠ LEDGER INCONSISTENCY: {lc['n_orphan_grades']} orphan grade(s) with no "
        f"matching trial record were excluded from analysis: {lc['orphan_grades']}"
    ]


def _integrity_line(findings: FindingsDocument) -> str:
    i = findings.integrity
    if i["rate"] is None:
        return "blinding integrity: n/a (no human review recorded yet)"
    return (
        f"blinding integrity rate: {_fmt(i['rate'], 3)} over {i['n_reviews']} review(s); "
        f"guess accuracy: {_fmt(i['guess_accuracy'], 3)}"
    )


def render_html(
    findings: FindingsDocument,
    ledger_path,
    mode: Literal["official", "exploratory"] = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Minimal self-contained HTML render; exploratory carries a fixed per-section banner."""
    md = render_markdown(
        findings, ledger_path, mode, metric=metric, corpus_manifest=corpus_manifest
    )
    banner = (
        ""
        if mode == "official"
        else f'<div class="watermark">{_WATERMARK}</div>'
    )
    # Each markdown section header becomes a section; the exploratory banner is
    # emitted before every <h2>/<h3> so the watermark is present per section.
    body_lines = []
    for line in md.splitlines():
        if mode != "official" and (line.startswith("## ") or line.startswith("### ")):
            body_lines.append(banner)
        # AN-5: escape the rendered content — an arm name / reason carrying markup
        # (e.g. a <script>) must land inert, not verbatim. The banner above is our
        # own trusted markup and is emitted unescaped.
        body_lines.append(f"<p>{_html.escape(line)}</p>")
    style = (
        "<style>.watermark{background:#fee;color:#900;padding:4px;"
        "font-weight:bold;border:1px solid #900;margin:6px 0}</style>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{style}</head><body>{''.join(body_lines)}</body></html>"
    )
