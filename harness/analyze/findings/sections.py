"""Findings sections — the section model + body builders [refactor 07 §1].

Each rendered findings section is a :class:`Section` (an AC-pinned ``title`` +
its exact markdown ``lines``). The body builders — the ``_*_lines`` functions the
dossier already imported privately — live here once, and the two mode sequences
:func:`official_sections` / :func:`exploratory_sections` assemble them into
ordered Section lists from the SAME builders. The two markdown renderers become
thin, uniform framers over these sequences, so the section ordering lives in one
module instead of being hand-maintained twice across the render functions. A new
surface (the dossier's disclosure blocks, the card) reads the same Section
objects rather than re-deriving a parallel list under a parity plea [refactor 07 §1].

Ordering note: the official and exploratory renders are deliberately DIFFERENT
documents — a single total order cannot reproduce both byte-for-byte (they order
control-reuse vs process, and provenance vs CI-method, oppositely), so each mode
keeps its own sequence builder; what is unified is the section MODEL and every
body builder they share.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .extract import MIN_DETECTION_CLUSTERS
from .model import ComparisonFinding, FindingsDocument, MDEBlock, display_mde


@dataclass(frozen=True)
class Section:
    """One rendered findings section: an AC-pinned ``title`` + its exact markdown
    ``lines`` [refactor 07 §1].

    ``data`` is the additive typed projection the section is about — the
    ``MDEBlock`` / ``ComparisonFinding`` a structured renderer can read instead
    of re-parsing the lines; minimal for now (populated where a section maps to
    one model, else ``None``)."""

    title: str
    lines: list[str]
    data: Optional[object] = None


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


def _fmt(x: Optional[float], dp: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{dp}f}"


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


_WATERMARK = "⚠ EXPLORATORY — not an official, pre-registered finding"


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


# --- the canonical section sequences [refactor 07 §1] ----------------------
def _official_comparison_lines(findings: FindingsDocument) -> list[str]:
    """The official ``Primary metric`` body: every comparison inline, excluded
    ones collapsed to a one-line header, each with its adjacent forensic flags."""
    lines: list[str] = []
    for cf in findings.comparisons:
        if cf.excluded_from_official:
            lines.append(f"### Comparison: {cf.label} — EXCLUDED ({cf.exclusion_reason})")
            continue
        lines.extend(_comparison_lines(cf, findings.mde))
        # EVAL-11 AC-5: flags render beside the comparison they affect
        lines.extend(_forensic_flags_for_comparison(findings, cf))
    return lines


def official_sections(findings: FindingsDocument) -> list[Section]:
    """The official render's ordered sections [AC-5]. The pre-registered primary
    metric + decision rule lead (in the render header); every comparison is inline
    under one ``Primary metric`` section; process rides an EXPLORATORY-labelled
    title, never a primary metric [AN-12]."""
    sections = [
        Section("Minimum detectable effect", _mde_lines(findings.mde), findings.mde),
        Section("Primary metric",
                _multi_arm_lines(findings) + _official_comparison_lines(findings)),
        Section("Confounds (disclosed, non-suppressing)",
                [f"- {c['flag']}" for c in findings.confounds] or ["- none"]),
        Section("Contamination (disclosed, non-suppressing)", _contamination_lines(findings)),
        Section("Blinding integrity", [f"- {_integrity_line(findings)}"]),
    ]
    tier = _tier_lines(findings)
    if tier:
        sections.append(Section("Grade tier", tier))
    consistency = _ledger_consistency_lines(findings)
    if consistency:
        sections.append(Section("Ledger consistency", consistency))
    override = _override_lines(findings)
    if override:
        sections.append(Section("Terminal overrides", override))
    judge_cov = _judge_coverage_lines(findings)
    if judge_cov:
        sections.append(Section("Judge coverage", judge_cov))
    if not findings.rubric_committed:
        sections.append(Section("Rubric commitment", [
            "- ⚠ CAVEAT: this experiment was locked before rubric commitment "
            "(D-P7-6); the judging rubric content is not pinned, so a post-lock "
            "rubric change cannot be detected from the ledger",
        ]))
    # AN-12 / REVIEW-D-3: the process section is retained in the official render
    # under an explicit EXPLORATORY/advisory label with the unblinded disclosure —
    # never a primary metric, never stripped (findings.json already hashes it into
    # findings_sha256, so stripping the markdown would desync from the artifact)
    # [EVAL-9 AC-6].
    if findings.process is not None:
        sections.append(Section(
            f"Process diagnostics — {_WATERMARK} (advisory secondary, NEVER a primary metric)",
            _process_lines(findings)))
    # EVAL-11 AC-5: disclosed in the OFFICIAL render too — disclosure-only,
    # never a fence input [D004]
    if findings.forensics is not None:
        sections.append(Section("Forensic flags (disclosed, non-suppressing)",
                                _forensics_lines(findings)))
    if findings.reuse is not None:
        sections.append(Section("Control reuse (disclosed — EXPLORATORY, never official)",
                                _reuse_lines(findings)))
    sections.append(Section("Provenance", _provenance_lines(findings)))
    return sections


def exploratory_sections(findings: FindingsDocument) -> list[Section]:
    """The exploratory render's ordered sections [AC-5, D003] — every one carries
    the watermark at framing time; each comparison is its own section, and the
    secondary/judge-calibration tiers appear here (never in the official render)."""
    sections = [
        Section("Pre-registered context", [
            f"- primary metric: {findings.primary_metric}",
            f"- decision rule: `{findings.decision_rule}`",
        ]),
        Section("Minimum detectable effect", _mde_lines(findings.mde), findings.mde),
    ]
    if findings.multi_arm:
        sections.append(Section("Multi-arm disclosure", _multi_arm_lines(findings)))
    for cf in findings.comparisons:
        sections.append(Section(
            f"Primary metric — {cf.label}",
            _comparison_lines(cf, findings.mde)
            # EVAL-11 AC-5: flags render beside the comparison they affect
            + _forensic_flags_for_comparison(findings, cf),
            cf,
        ))
    if findings.reuse is not None:
        sections.append(Section("Control reuse (EXPLORATORY, unpaired)", _reuse_lines(findings)))
    sections.append(Section("Secondary metrics (exploratory)", _secondary_lines(findings)))
    if findings.judge_calibration is not None:
        sections.append(Section("Judge calibration (per class)", _judge_calibration_lines(findings)))
    if findings.process is not None:
        sections.append(Section("Process diagnostics (EXPLORATORY secondary)", _process_lines(findings)))
    if findings.forensics is not None:
        sections.append(Section("Forensic flags (disclosed, non-suppressing)", _forensics_lines(findings)))
    sections.append(Section("Confounds (disclosed, non-suppressing)",
                            [f"- {c['flag']}: {c}" for c in findings.confounds] or ["- none"]))
    sections.append(Section("Contamination (disclosed, non-suppressing)", _contamination_lines(findings)))
    sections.append(Section("Blinding integrity", [f"- {_integrity_line(findings)}"]))
    tier = _tier_lines(findings)
    if tier:
        sections.append(Section("Grade tier", tier))
    consistency = _ledger_consistency_lines(findings)
    if consistency:
        sections.append(Section("Ledger consistency", consistency))
    override = _override_lines(findings)
    if override:
        sections.append(Section("Terminal overrides", override))
    judge_cov = _judge_coverage_lines(findings)
    if judge_cov:
        sections.append(Section("Judge coverage", judge_cov))
    sections.append(Section("CI method selection (coverage)", [f"- {findings.ci_selection}"]))
    sections.append(Section("Provenance", _provenance_lines(findings)))
    return sections
