"""Findings HTML renderer [refactor 07 §1].

Commit N of the report.py decomposition moves today's escaped-markdown HTML
render here verbatim (behavior-identical); the real HTML renderer over the
section model is the SEPARATE sanctioned output change that follows.
"""

from __future__ import annotations

import html as _html
from typing import Literal, Optional

from .model import FindingsDocument
from .render_md import render_markdown
from .sections import _WATERMARK


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
