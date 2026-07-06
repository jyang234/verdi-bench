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
from typing import Literal, Optional

from ..contamination.summary import latest_probe, probe_asymmetries
from ..ledger import events
from ..ledger.query import ledger_head_hash, verify
from ..ledger.view import LedgerView
from .findings.extract import (  # noqa: F401 — facade re-export while importers migrate [refactor 07 §1]
    METRICS,
    MIN_DETECTION_CLUSTERS,
    MetricDef,
    PerTaskSeries,
    _apply_holm,
    _attribution_metrics,
    _comparison_series,
    _forensics_section,
    _holdout_values,
    _integrity,
    _judge_calibration,
    _judge_coverage,
    _judge_preference_by_task,
    _judge_preference_rates,
    _judge_summary,
    _ledger_consistency,
    _lock_event,
    _mde_block,
    _mean,
    _orphan_grades,
    _override_summary,
    _paired_arm_series,
    _process_section,
    _quarantine_entries,
    _quarantined_comparison_ids,
    _quarantined_trial_ids,
    _reuse_judge_winrate,
    _reuse_section,
    _reused_holdout_by_task,
    _reused_telemetry_by_task,
    _secondary_metrics,
    _telemetry_values,
    _tier_summary,
    _trial_index,
    _two_sided_bootstrap_p,
    compute_findings,
    metric_def,
    paired_task_rows,
    per_arm_absolute_scores,
)
from .findings.model import (  # noqa: F401 — facade re-export while importers migrate [refactor 07 §1]
    AnalyzeError,
    AsymmetricContaminationError,
    CalibrationIncompleteError,
    CantAnalyzeReason,
    ComparisonFinding,
    ComparisonStats,
    CorpusMismatchError,
    CorrectionMismatchError,
    Decision,
    DisclosureError,
    EffectBlock,
    FindingsDocument,
    InsulationAlarmError,
    MDEBlock,
    Provenance,
    ProvenanceError,
    RubricMismatchError,
    SelfcheckRequiredError,
    UnregisteredOfficialError,
    cant_analyze_reason,
    display_mde,
)


def _judge_coverage_lines(findings: FindingsDocument) -> list[str]:
    """Disclosure lines for terminal CANT_JUDGE exclusions [F-M-J1] — shared by
    the markdown render and the dossier disclosure sections; [] when the judge
    never ran or every comparison was judged."""
    jc = findings.judge_coverage
    if not jc or not jc.get("cant_judge"):
        return []
    detail = ", ".join(f"{r}: {n}" for r, n in jc["cant_judge"].items())
    lines = [
        f"- {jc['terminal_cant_judge']} of {jc['verdicts']} judged comparison(s) "
        f"terminally unjudgeable ({detail}) — excluded from judge_preference and "
        "calibration, never imputed. If exclusions correlate with outcomes "
        "(e.g. an arm salting canaries on losing trials), judge_preference is "
        "biased by this missing-data channel [F-M-J1]."
    ]
    leaks = jc.get("identity_leak_by_class") or {}
    if leaks:
        by_class = ", ".join(f"{cls}: {n}" for cls, n in leaks.items())
        lines.append(
            f"- identity_leak by task class ({by_class}) — a rate concentrated "
            "in one class can signal an over-broad scrub pattern rather than a "
            "real leak [F-M-J2]."
        )
    return lines


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
    mde_val = _fmt(display_mde(mde))  # honest at the realized N [F-M-S3]
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


def _reuse_lines(findings: FindingsDocument) -> list[str]:
    """The control-reuse disclosure lines [control-reuse plan] — shared by both
    renders. Always exploratory, always unpaired, never a decision."""
    r = findings.reuse
    if not r:
        return []
    lines = [
        f"- ⚠ reused control arm {r['control_arm']!r} from source "
        f"{r['source_experiment_id']} (fingerprint {str(r['fingerprint_digest'])[:12]}…) "
        "— UNPAIRED, exploratory-only, never an official decision",
    ]
    comp = r.get("computed")
    if comp:
        lines.append(
            f"- {comp['metric']} [unpaired]: control mean={_fmt(comp['control_mean'])} "
            f"(n={comp['control_n_tasks']}), contender mean={_fmt(comp['contender_mean'])} "
            f"(n={comp['contender_n_tasks']}), "
            f"Δ(contender−control)={_fmt(comp['delta_contender_minus_control'])}"
        )
    jp = r.get("judge_preference")
    if jp:
        lines.append(
            f"- judge preference [judgment, unpaired]: contender win-rate "
            f"{_fmt(jp['contender_win_rate'])} over {jp['decided']} decided comparison(s)"
        )
    lines.append(f"- {r['disclosure']}")
    return lines


def _mde_lines(mde: MDEBlock) -> list[str]:
    lines = [f"MDE = {_fmt(mde.value)}"]
    if mde.achieved_value is not None:
        lines.append(
            f"  achieved at realized n_tasks={mde.realized_n_tasks}: "
            f"\u2248{_fmt(mde.achieved_value)} (plan-time MDE scaled 1/\u221an — "
            "quarantines/missing grades shrank the realized clusters) [F-M-S3]"
        )
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
    return {ev["trial_record"]["task_id"] for ev in LedgerView(ledger_path).by_kind(events.TRIAL)}


def _ledgered_calibration_status(ledger_path, corpus_id: str, semver: str) -> Optional[str]:
    """The latest calibration status **on the chain** for a corpus, or None.

    Reads ``calibration_run`` events (last-write-wins in ledger order) rather than
    the hand-editable ``manifest.calibration.status`` [CO-4]."""
    status = None
    for ev in LedgerView(ledger_path).by_kind(events.CALIBRATION_RUN):
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
    # 7. holdout-leak insulation alarms [F-M-C3, EVAL-4 AC-9]: an alarm on the
    # latest ledgered probe is an insulation VIOLATION — holdout content
    # reproduced in a solution — and refuses the official render until it is
    # investigated: quarantine the offending trial (ledgered), re-run the scan
    # (quarantined trials are skipped, disclosed) and the probe.
    _assert_no_insulation_alarms(ledger_path)
    # 8. multi-arm correction consistency [F-H7]: one pre-registered decision
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


def effective_multi_arm_correction(spec) -> str:
    """The multi-arm correction an official render of ``spec`` would apply —
    the value the render fence gates on [F-H7, refactor 01 §4 D8].

    Mirrors ``compute_findings``: the ``multi_arm`` block (and with it a
    correction) exists only for a >2-arm family — one comparison is built per
    arm beyond ``arms[0]``, so with two arms there is a single pre-registered
    pair, no decision family, and the render passes ``"none"`` regardless of
    the spec field. The observer fence (``analyze/fence.py``) evaluates the
    correction-consistency item with this value so it cannot show ready while
    the render refuses; structurally unified with the render in Phase 5."""
    return spec.multi_arm_correction if len(spec.arms) > 2 else "none"


def _assert_correction_consistent(correction: str, ledger_path) -> None:
    """Refuse an official render whose applied multi-arm correction differs from
    any prior official render's recorded correction [F-H7]. Render events that
    predate the recorded field are skipped — a legacy chain is not refused on,
    but the first post-field official render pins the procedure for the rest."""
    for ev in LedgerView(ledger_path).by_kind(events.FINDINGS_RENDERED):
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
    out += ["", "## Blinding integrity", f"- {_integrity_line(findings)}"]
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
    if findings.reuse is not None:
        out += [
            "",
            "## Control reuse (disclosed — EXPLORATORY, never official)",
            *_reuse_lines(findings),
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
    if findings.reuse is not None:
        out += section("Control reuse (EXPLORATORY, unpaired)", _reuse_lines(findings))
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
