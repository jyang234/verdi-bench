"""Findings markdown renderer [refactor 07 §1, refactor 11 §G3].

The official/exploratory framing + watermark policy over the canonical section
sequences (:mod:`.sections`), now a
:class:`~harness.analyze.findings.render.Renderer` registered under the ``md``
format id. ``render_markdown`` funnels through the shared
:func:`~harness.analyze.findings.render.render_findings` dispatch (fence +
section-select + frame), so the framing consumes the Section sequence handed to
it rather than re-deriving it. Output is byte-identical to the previous
``report.py`` renderer (the golden md fixtures are the proof).
"""

from __future__ import annotations

from typing import Literal, Optional, Sequence

from .model import FindingsDocument
from .render import RenderContext, register_renderer, render_findings
from .sections import Section, _WATERMARK


def _render_official_md(findings: FindingsDocument, sections: Sequence[Section]) -> str:
    """Frame the official section sequence [AC-5]: the pre-registered header, one
    ``## title`` per section, then the coverage CI-method footer. The ordering +
    bodies are :func:`~harness.analyze.findings.sections.official_sections`."""
    out = [
        f"# Official findings — {findings.experiment_id}",
        f"Pre-registered primary metric: **{findings.primary_metric}**",
        f"Decision rule: `{findings.decision_rule}`",
    ]
    for sec in sections:
        out += ["", f"## {sec.title}", *sec.lines]
    out += ["", f"CI method selected by coverage: {findings.ci_selection['selected_method']}"]
    return "\n".join(out) + "\n"


def _render_exploratory_md(findings: FindingsDocument, sections: Sequence[Section]) -> str:
    """Frame the exploratory section sequence [AC-5, D003]: the watermark leads,
    then every section is wrapped with the watermark on its own header. The
    ordering + bodies are
    :func:`~harness.analyze.findings.sections.exploratory_sections`."""
    out = [f"# Findings (EXPLORATORY) — {findings.experiment_id}", _WATERMARK, ""]
    for sec in sections:
        # watermark on EVERY section header [AC-5, D003]
        out += [f"## {_WATERMARK}", f"### {sec.title}", *sec.lines, ""]
    return "\n".join(out) + "\n"


class _MarkdownRenderer:
    """The ``md`` findings renderer — frames a Section sequence to markdown, the
    official vs exploratory framing chosen by ``ctx.mode`` [refactor 11 §G3]."""

    format_id = "md"

    def render(self, sections: Sequence[Section], ctx: RenderContext) -> str:
        if ctx.mode == "official":
            return _render_official_md(ctx.findings, sections)
        return _render_exploratory_md(ctx.findings, sections)


register_renderer(_MarkdownRenderer())


def render_markdown(
    findings: FindingsDocument,
    ledger_path,
    mode: Literal["official", "exploratory"] = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Render findings to markdown behind the pre-registration fence [refactor 11 §G3]."""
    return render_findings(
        "md", findings, ledger_path, mode, metric=metric, corpus_manifest=corpus_manifest
    )
