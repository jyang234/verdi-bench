"""The findings Renderer seam — a format registry over the Section model [refactor 11 §G3].

Mirrors the ``CIMethod`` registry (:mod:`harness.analyze.ci`) the master plan §2
designates as THE model to copy: a :class:`Renderer` Protocol + concrete
instances + a dict keyed by format id + ``resolve``/``available`` helpers. A new
output format becomes one :class:`Renderer` over the shared
:class:`~harness.analyze.findings.sections.Section` sequence instead of a fork of
a render function [refactor 07 §1].

The ``md`` and ``html`` findings renderers register themselves on import (the
:func:`register_renderer` + bottom-import pattern of
:mod:`harness.grade.plugins`); the dossier registers from
:mod:`harness.analyze.dossier`. :func:`render_findings` is the single md/html
dispatch — fence, canonical section-select, then frame through the resolved
renderer — that ``render_markdown`` / ``render_html`` and ``analyze/api.py`` all
funnel through, so the format choice is a registry lookup instead of a boolean
ternary [refactor 11 §G3].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from .fence import validate_for_render
from .model import FindingsDocument
from .sections import Section, exploratory_sections, official_sections


@dataclass(frozen=True)
class RenderContext:
    """The inputs every findings renderer already receives [refactor 11 §G3].

    The computed ``findings`` document plus the ``ledger_path``, the render
    ``mode`` (``official`` | ``exploratory``), and the ``official``-render gates
    (``metric`` + ``corpus_manifest``) — nothing more; a renderer reads only the
    fields its format needs, exactly as ``interval`` on a ``CIMethod`` ignores the
    inputs a percentile interval does not use."""

    findings: FindingsDocument
    ledger_path: object
    mode: str
    metric: Optional[str] = None
    corpus_manifest: object = None


class Renderer(Protocol):
    """One findings output format: its ``format_id`` and a ``render`` that frames a
    :class:`Section` sequence into that format's bytes [refactor 11 §G3]."""

    format_id: str

    def render(self, sections: Sequence[Section], ctx: RenderContext) -> str:
        ...


_RENDERERS: dict[str, Renderer] = {}


def register_renderer(renderer: Renderer) -> None:
    """Register ``renderer`` under its ``format_id`` [refactor 11 §G3].

    Called at module import (the :mod:`harness.grade.plugins` pattern): importing
    a renderer module registers its format, so the registry reflects the surfaces
    the caller has loaded."""
    _RENDERERS[renderer.format_id] = renderer


def resolve_renderer(fmt: "str | Renderer") -> Renderer:
    """The renderer registered under a format id, or ``fmt`` itself if already a
    :class:`Renderer`. An unknown id fails loudly with the closed choice list —
    never a silent default [refactor 11 §G3, mirrors ``resolve_ci_method``]."""
    if not isinstance(fmt, str):
        return fmt
    try:
        return _RENDERERS[fmt]
    except KeyError:
        raise ValueError(
            f"unknown render format {fmt!r}; expected one of {sorted(_RENDERERS)}"
        ) from None


def available_renderers() -> list[str]:
    """The registered format ids, sorted [refactor 11 §G3]."""
    return sorted(_RENDERERS)


def render_findings(
    fmt: str,
    findings: FindingsDocument,
    ledger_path,
    mode: str = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Render the findings document to ``fmt`` (``md`` | ``html``) behind the fence
    [refactor 11 §G3].

    The single md/html path: validate through the shared render fence, select the
    canonical section sequence for the mode (the official and exploratory renders
    order their sections differently, so each mode keeps its own builder), then
    frame through the resolved :class:`Renderer`. ``render_markdown`` /
    ``render_html`` and ``analyze/api.py`` all funnel here, so the format is a
    registry lookup rather than a hard-coded branch."""
    validate_for_render(
        findings, ledger_path, mode, metric=metric, corpus_manifest=corpus_manifest
    )
    ctx = RenderContext(findings, ledger_path, mode, metric, corpus_manifest)
    sections = official_sections(findings) if mode == "official" else exploratory_sections(findings)
    return resolve_renderer(fmt).render(sections, ctx)


# The findings-package renderers register on import of THIS module (the
# register_renderer + bottom-import pattern of harness.grade.plugins): they import
# the seam names defined above, so the import must ride the bottom of this body.
from . import render_html as _render_html  # noqa: E402,F401
from . import render_md as _render_md  # noqa: E402,F401
