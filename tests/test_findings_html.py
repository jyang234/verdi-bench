"""The real findings HTML renderer [refactor 07 §1 — the one sanctioned output change].

``render_html`` was escaped markdown in ``<p>`` tags; it is now a real renderer
over the :class:`~harness.analyze.findings.sections.Section` sequences (one
``<h2>`` per section, ``<ul>``/``<li>`` disclosure bullets, ``<h3>`` sub-headers).
No golden byte-fixture pins its output and no prior test pinned the ``<p>``-per-
line form (they pin the SEMANTIC invariants: escaped, watermarked, self-contained
``<!doctype html>``); these tests pin those invariants on the upgraded renderer.
"""

from __future__ import annotations

from harness.analyze.findings.render_html import render_html
from harness.analyze.findings.sections import exploratory_sections
from harness.analyze.report import compute_findings
from tests.fixtures.builders import ctx_for, locked_experiment, seed_trial_and_grade
from tests.fixtures.scenarios import FAST_STATS, populate_paired_trials


def _paired_findings(tmp_path):
    ctx = ctx_for(tmp_path / "e")
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    populate_paired_trials(ledger, ctx, control_pass=lambda i: True, treatment_pass=lambda i: True)
    return compute_findings(ledger, spec, spec.seed, **FAST_STATS), ledger


def _evil_findings(tmp_path):
    """A findings doc whose control arm NAME carries markup — the AN-5 case."""
    ctx = ctx_for(tmp_path / "e")
    evil = "ctl<script>alert(1)</script>"
    arms = [
        {"name": evil, "platform": "claude_code",
         "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
        {"name": "treatment", "platform": "codex", "model": "openai/gpt-4o-2024-08-06", "payload": {}},
    ]
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx, arms=arms)
    for i in range(3):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c{i}", task_id=f"t{i}", arm=evil,
                             passed=True, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"x{i}", task_id=f"t{i}", arm="treatment",
                             passed=False, provenance={"image_digest": "d"})
    return compute_findings(ledger, spec, spec.seed, **FAST_STATS), ledger


def test_real_html_structure_over_sections(tmp_path):
    """One <h1>, one <h2> per canonical section, and real list markup — the
    renderer walks the section model, not re-parsed markdown [refactor 07 §1]."""
    findings, ledger = _paired_findings(tmp_path)
    html = render_html(findings, ledger, "exploratory")
    assert html.count("<h1>") == 1
    assert html.count("<h2>") == len(exploratory_sections(findings))
    assert "<ul>" in html and "<li>" in html
    assert "<section>" in html


def test_html_is_self_contained(tmp_path):
    """Self-contained [AC-3 posture]: a doctype + inline <style>, and NOT a single
    external reference or script — the artifact renders offline and inert."""
    findings, ledger = _paired_findings(tmp_path)
    html = render_html(findings, ledger, "exploratory")
    assert "<!doctype html>" in html
    assert "<style>" in html and "</style>" in html
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html.lower()
    assert "url(" not in html  # no external CSS assets
    assert "src=" not in html and "<link" not in html


def test_html_escapes_finding_derived_markup(tmp_path):
    """AN-5: a <script> in an arm name lands inert (escaped), never verbatim."""
    findings, ledger = _evil_findings(tmp_path)
    html = render_html(findings, ledger, "exploratory")
    assert "<script>alert(1)</script>" not in html  # never verbatim
    assert "&lt;script&gt;" in html                  # escaped


def test_html_is_byte_deterministic(tmp_path):
    """A pure function of the findings doc: two renders are byte-identical."""
    findings, ledger = _paired_findings(tmp_path)
    a = render_html(findings, ledger, "exploratory")
    b = render_html(findings, ledger, "exploratory")
    assert a == b


def test_exploratory_watermarks_every_section(tmp_path):
    """The exploratory watermark rides a banner before EVERY section, plus the
    leading banner [AC-5, D003]."""
    findings, ledger = _paired_findings(tmp_path)
    expl = render_html(findings, ledger, "exploratory")
    banners = expl.count('<div class="watermark">')
    assert banners == len(exploratory_sections(findings)) + 1  # per-section + the lead banner
    assert "EXPLORATORY" in expl
