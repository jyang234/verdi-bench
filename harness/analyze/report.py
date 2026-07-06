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
from .findings.sections import (  # noqa: F401 — facade re-export while importers migrate [refactor 07 §1]
    Section,
    _comparison_lines,
    _contamination_lines,
    _fmt,
    _forensic_flags_for_comparison,
    _forensics_lines,
    _integrity_line,
    _judge_calibration_lines,
    _judge_coverage_lines,
    _ledger_consistency_lines,
    _mde_lines,
    _multi_arm_lines,
    _override_lines,
    _process_lines,
    _provenance_lines,
    _reuse_lines,
    _secondary_lines,
    _tier_lines,
    _WATERMARK,
    asymmetry_line,
    exploratory_sections,
    official_sections,
)


# --- rendering + the fence -------------------------------------------------
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


def _render_official_md(findings: FindingsDocument) -> str:
    """Frame the official section sequence [AC-5]: the pre-registered header, one
    ``## title`` per section, then the coverage CI-method footer. The ordering +
    bodies are :func:`~harness.analyze.findings.sections.official_sections`."""
    out = [
        f"# Official findings — {findings.experiment_id}",
        f"Pre-registered primary metric: **{findings.primary_metric}**",
        f"Decision rule: `{findings.decision_rule}`",
    ]
    for sec in official_sections(findings):
        out += ["", f"## {sec.title}", *sec.lines]
    out += ["", f"CI method selected by coverage: {findings.ci_selection['selected_method']}"]
    return "\n".join(out) + "\n"


def _render_exploratory_md(findings: FindingsDocument) -> str:
    """Frame the exploratory section sequence [AC-5, D003]: the watermark leads,
    then every section is wrapped with the watermark on its own header. The
    ordering + bodies are
    :func:`~harness.analyze.findings.sections.exploratory_sections`."""
    out = [f"# Findings (EXPLORATORY) — {findings.experiment_id}", _WATERMARK, ""]
    for sec in exploratory_sections(findings):
        # watermark on EVERY section header [AC-5, D003]
        out += [f"## {_WATERMARK}", f"### {sec.title}", *sec.lines, ""]
    return "\n".join(out) + "\n"


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
