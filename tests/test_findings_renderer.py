"""The findings Renderer seam — protocol + format registry [refactor 11 §G3].

The seam mirrors the ``CIMethod`` registry: a format id resolves to a
:class:`Renderer` that frames a :class:`Section` sequence, an unknown id fails
loudly with the closed choice list, and the registry dispatch produces the SAME
bytes as the public ``render_markdown`` / ``render_html`` (which now funnel
through it). ``RenderContext`` carries only the inputs the existing renderers
already receive — no speculative fields.
"""

from __future__ import annotations

import pytest

from harness.analyze.findings.render import (
    RenderContext,
    available_renderers,
    render_findings,
    resolve_renderer,
)
from harness.analyze.findings.render_html import render_html
from harness.analyze.findings.render_md import render_markdown
from harness.analyze.findings.sections import exploratory_sections, official_sections
from harness.analyze.report import compute_findings
from tests.fixtures.builders import ctx_for, locked_experiment
from tests.fixtures.scenarios import FAST_STATS, populate_paired_trials


def _paired_findings(tmp_path):
    ctx = ctx_for(tmp_path / "e")
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    populate_paired_trials(
        ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True
    )
    return compute_findings(ledger, spec, spec.seed, **FAST_STATS), ledger


def test_findings_formats_are_registered():
    """Importing the seam registers the ``md`` and ``html`` findings renderers."""
    assert {"md", "html"} <= set(available_renderers())


def test_resolve_returns_the_matching_format():
    assert resolve_renderer("md").format_id == "md"
    assert resolve_renderer("html").format_id == "html"


def test_resolve_unknown_format_fails_loud():
    """An unknown id raises with the closed choice list — never a silent default
    [mirrors ``resolve_ci_method``]."""
    with pytest.raises(ValueError) as excinfo:
        resolve_renderer("pdf")
    msg = str(excinfo.value)
    assert "pdf" in msg and "'md'" in msg and "'html'" in msg


def test_resolve_passes_a_renderer_instance_through():
    md = resolve_renderer("md")
    assert resolve_renderer(md) is md


def test_render_context_carries_only_the_render_inputs():
    """RenderContext is exactly what the existing renderers receive — findings +
    ledger + mode + the official gates, no speculative fields [refactor 11 §G3]."""
    assert set(RenderContext.__dataclass_fields__) == {
        "findings",
        "ledger_path",
        "mode",
        "metric",
        "corpus_manifest",
    }


def test_registry_dispatch_is_byte_identical_to_public_renderers(tmp_path):
    """Framing the mode's canonical Section sequence through the resolved renderer
    yields the SAME bytes as the public render — the registry is the one path."""
    findings, ledger = _paired_findings(tmp_path)
    ctx = RenderContext(findings, ledger, "exploratory")
    sections = exploratory_sections(findings)
    assert resolve_renderer("md").render(sections, ctx) == render_markdown(
        findings, ledger, "exploratory"
    )
    assert resolve_renderer("html").render(sections, ctx) == render_html(
        findings, ledger, "exploratory"
    )


def test_render_findings_dispatches_by_format(tmp_path):
    """``render_findings`` selects the renderer by id, so ``md`` and ``html`` are
    the markdown and HTML documents respectively (the api.py dispatch path)."""
    findings, ledger = _paired_findings(tmp_path)
    md = render_findings("md", findings, ledger, "exploratory")
    html = render_findings("html", findings, ledger, "exploratory")
    assert md.startswith("# Findings (EXPLORATORY)")
    assert html.startswith("<!doctype html>")
    assert md == render_markdown(findings, ledger, "exploratory")
    assert html == render_html(findings, ledger, "exploratory")


def test_dossier_registers_on_import():
    """The dossier is a findings surface behind the registry too [refactor 11 §G3]:
    importing it registers its layer-wrapping under the ``dossier`` format id."""
    import harness.analyze.dossier  # noqa: F401 — registers on import

    assert "dossier" in available_renderers()
    assert resolve_renderer("dossier").format_id == "dossier"


def test_official_and_exploratory_frame_their_own_sequences(tmp_path):
    """The seam frames the mode's canonical sequence; official leads with the
    pre-registered header, exploratory with the watermark."""
    findings, ledger = _paired_findings(tmp_path)
    ctx = RenderContext(findings, ledger, "exploratory")
    md = resolve_renderer("md").render(exploratory_sections(findings), ctx)
    # every exploratory section rides the watermark header
    assert md.count("## ⚠ EXPLORATORY") == len(exploratory_sections(findings))
    # the official sequence differs (its own builder, distinct ordering)
    assert official_sections(findings) != exploratory_sections(findings)
