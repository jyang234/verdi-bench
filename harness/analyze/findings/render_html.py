"""Findings HTML renderer — a real renderer over the section model [refactor 07 §1].

The ONE sanctioned output change of the report.py decomposition (07 §1): instead
of escaping the finished markdown line-by-line into ``<p>`` tags, this renders the
same canonical :class:`~harness.analyze.findings.sections.Section` sequences
(:func:`official_sections` / :func:`exploratory_sections`) into semantic HTML —
one ``<h2>`` per section, ``<ul>``/``<li>`` for the disclosure bullets, ``<h3>``
for a comparison sub-header — behind the same :func:`validate_for_render` fence.

Invariants preserved [AN-5, AC-5, D003]: every finding-derived value is escaped
(an arm name carrying ``<script>`` lands inert); the exploratory watermark rides
a banner before EVERY section; the document is self-contained (inline CSS, no
external references, no scripts) and byte-deterministic for a fixed findings doc.
"""

from __future__ import annotations

import html as _html
import re
from typing import Literal, Optional

from .fence import validate_for_render
from .model import FindingsDocument
from .sections import Section, _WATERMARK, exploratory_sections, official_sections

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")

_STYLE = (
    "body{font:14px/1.5 system-ui,sans-serif;margin:1.5rem;max-width:60rem}"
    "h1{font-size:1.5rem}h2{font-size:1.15rem;margin:0 0 .3rem}h3{font-size:1rem}"
    "section{border:1px solid #ccc;border-radius:6px;padding:.4rem 1rem;margin:1rem 0}"
    "ul{margin:.2rem 0}code{background:#f2f2f2;padding:0 .2rem;border-radius:3px}"
    ".sub{list-style:circle;margin-left:1rem}"
    ".watermark{background:#fee;color:#900;padding:4px 8px;font-weight:bold;"
    "border:1px solid #900;margin:6px 0}"
)


def _inline(text: str) -> str:
    """Escape a finding-derived line, then lift its markdown emphasis/code spans
    to ``<strong>``/``<code>`` [AN-5]. ``html.escape`` runs first, so any ``<``/
    ``&`` in the content stays inert; ``*`` and `` ` `` are not HTML-special, so
    the span markers survive to be converted on the already-escaped text."""
    t = _html.escape(text)
    t = _BOLD.sub(r"<strong>\1</strong>", t)
    t = _CODE.sub(r"<code>\1</code>", t)
    return t


def _body_html(lines: list[str]) -> str:
    """One section's body lines → semantic HTML: bullets become ``<li>`` (a
    2-space indent marks a nested item), a ``###`` line an ``<h3>``, anything
    else a ``<p>``. Every value is escaped [AN-5]; deterministic per input."""
    out: list[str] = []
    in_list = False

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            cls = ' class="sub"' if (len(line) - len(stripped)) >= 2 else ""
            out.append(f"<li{cls}>{_inline(stripped[2:])}</li>")
        elif stripped.startswith("### "):
            _close_list()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif line == "":
            _close_list()
        else:
            _close_list()
            out.append(f"<p>{_inline(line)}</p>")
    _close_list()
    return "".join(out)


def _section_html(sec: Section, watermark: str) -> str:
    banner = f'<div class="watermark">{watermark}</div>' if watermark else ""
    return f'{banner}<section><h2>{_html.escape(sec.title)}</h2>{_body_html(sec.lines)}</section>'


def render_html(
    findings: FindingsDocument,
    ledger_path,
    mode: Literal["official", "exploratory"] = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Render findings to a self-contained HTML document behind the fence."""
    validate_for_render(
        findings, ledger_path, mode, metric=metric, corpus_manifest=corpus_manifest
    )
    if mode == "official":
        title = f"Official findings — {findings.experiment_id}"
        head = [
            f"<p>Pre-registered primary metric: <strong>{_html.escape(findings.primary_metric)}</strong></p>",
            f"<p>Decision rule: <code>{_html.escape(findings.decision_rule)}</code></p>",
        ]
        sections = official_sections(findings)
        # official's coverage CI-method footer is a bare line, not a section [AC-5]
        method = findings.ci_selection["selected_method"]
        foot = [f"<p>CI method selected by coverage: {_html.escape(method)}</p>"]
        watermark = ""
    else:
        title = f"Findings (EXPLORATORY) — {findings.experiment_id}"
        watermark = _html.escape(_WATERMARK)
        head = [f'<div class="watermark">{watermark}</div>']
        sections = exploratory_sections(findings)
        foot = []

    body = "".join(
        [f"<h1>{_html.escape(title)}</h1>", *head]
        + [_section_html(sec, watermark) for sec in sections]
        + foot
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_html.escape(title)}</title><style>{_STYLE}</style>"
        f"</head><body>{body}</body></html>"
    )
