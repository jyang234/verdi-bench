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

from .findings.fence import (  # noqa: F401 — facade re-export while importers migrate [refactor 07 §1]
    FENCE_CHECKS,
    FenceCheck,
    FenceContext,
    FenceOutcome,
    _assert_correction_consistent,
    _assert_head_hash,
    _assert_no_insulation_alarms,
    _ledgered_calibration_status,
    _task_ids_run,
    _validate_process_disclosure,
    _validate_provenance,
    assert_official_fence,
    effective_multi_arm_correction,
    validate_for_render,
)
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


# --- rendering (the fence lives in findings/fence.py) ----------------------
def render_markdown(
    findings: FindingsDocument,
    ledger_path,
    mode: Literal["official", "exploratory"] = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Render findings to markdown behind the pre-registration fence."""
    validate_for_render(
        findings, ledger_path, mode, metric=metric, corpus_manifest=corpus_manifest
    )
    if mode == "official":
        return _render_official_md(findings)
    return _render_exploratory_md(findings)


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
