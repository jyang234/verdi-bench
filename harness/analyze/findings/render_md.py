"""Findings markdown renderers [refactor 07 §1].

The official/exploratory framing + watermark policy over the single canonical
section sequences (:mod:`.sections`), behind the shared pre-registration fence
(:func:`.fence.validate_for_render`). Output is byte-identical to the previous
``report.py`` renderers (the golden md fixtures are the proof).
"""

from __future__ import annotations

from typing import Literal, Optional

from .fence import validate_for_render
from .model import FindingsDocument
from .sections import _WATERMARK, exploratory_sections, official_sections


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
