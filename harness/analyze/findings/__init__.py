"""The findings package — ``analyze/report.py`` decomposed by concern [refactor 07 §1].

One subsystem per file: :mod:`model` (schema + refusal taxonomy), :mod:`extract`
(ledger→series + document computation), :mod:`sections` (the canonical section
sequence), :mod:`fence` (the one ordered fence-check list), :mod:`render_md` /
:mod:`render_html` (the renderers). ``harness.analyze.report`` stays a
re-exporting facade over this package while importers migrate.
"""

from __future__ import annotations

from .model import (
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

__all__ = [
    "AnalyzeError",
    "AsymmetricContaminationError",
    "CalibrationIncompleteError",
    "CantAnalyzeReason",
    "ComparisonFinding",
    "ComparisonStats",
    "CorpusMismatchError",
    "CorrectionMismatchError",
    "Decision",
    "DisclosureError",
    "EffectBlock",
    "FindingsDocument",
    "InsulationAlarmError",
    "MDEBlock",
    "Provenance",
    "ProvenanceError",
    "RubricMismatchError",
    "SelfcheckRequiredError",
    "UnregisteredOfficialError",
    "cant_analyze_reason",
    "display_mde",
]
