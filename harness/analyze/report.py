"""Findings — a re-exporting facade over the ``findings`` package [EVAL-6 §M4, refactor 07 §1].

The 2,275-line module that fused six concerns is now
:mod:`harness.analyze.findings`: :mod:`~harness.analyze.findings.model` (schema +
refusal taxonomy), :mod:`~harness.analyze.findings.extract` (ledger→series +
``compute_findings``), :mod:`~harness.analyze.findings.sections` (the section
model + canonical sequences), :mod:`~harness.analyze.findings.fence` (the one
official-fence check list), and the :mod:`~harness.analyze.findings.render_md` /
:mod:`~harness.analyze.findings.render_html` renderers.

This module re-exports every name the siblings and tests import from
``harness.analyze.report`` — the underscore-privates dossier/selfcheck/fence
bound, and the public ``compute_findings`` / ``render_markdown`` /
``render_html`` / ``metric_def`` / … — so out-of-tree importers keep working
while they migrate onto the package's now-public seams.

The pre-registration fence is still mechanical: ``compute_findings`` is a pure
reproducible function of ``(ledger, spec, seed, corpus_manifest)``; **official**
renders only the pre-registered primary metric + decision rule and is refused for
anything unregistered [AC-5] or an uncalibrated corpus [EVAL-8 AC-2]; every other
render carries the EXPLORATORY watermark [AC-5, D003]; the provenance block is
schema-required and the head hash is cross-checked at render time [AC-6]; a metric
with asymmetric nulls is excluded, never imputed [AC-7].
"""

from __future__ import annotations

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


from .findings.render_md import (  # noqa: F401 — facade re-export while importers migrate [refactor 07 §1]
    _render_exploratory_md,
    _render_official_md,
    render_markdown,
)
from .findings.render_html import render_html  # noqa: F401 — facade re-export [refactor 07 §1]
