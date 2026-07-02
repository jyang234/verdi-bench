"""Static, offline human review packet [EVAL-7 §M3, D001, AC-1, AC-3].

A self-contained HTML bundle per experiment: side-by-side response diffs, holdout
results, and the task prompt — ordered disagreements-first (without labeling
*why* an item is mandatory, which would leak the judge's state). It deliberately
**excludes** judge verdicts and arm identities: revealing the judge's opinion or
the arm before the human's verdict is a partial unblind [derived from
EVAL-2-D002 / blinding-measured].

Offline by construction: inline CSS only, no external requests, diffs
pre-rendered server-side with :mod:`difflib`. Every text field passes through
:func:`harness.review.scrub.blind_scrub`, and the finished HTML is re-scanned —
if any identity canary survives, packet generation is **blocked** (fail closed).
"""

from __future__ import annotations

import difflib
import html
import json
from dataclasses import dataclass, field

from .scrub import assert_identity_free, blind_scrub

# difflib table classes, styled inline so the bundle needs no external stylesheet.
_STYLE = """
body{font-family:system-ui,monospace;margin:1.5rem;color:#111}
h1{font-size:1.2rem} h2{font-size:1rem;border-top:1px solid #ccc;padding-top:.6rem}
table.diff{font-family:monospace;border-collapse:collapse;width:100%;font-size:12px}
.diff_header{background:#eee;color:#666;text-align:right;padding:0 .4rem}
td{vertical-align:top;white-space:pre-wrap}
.diff_next{background:#f7f7f7}
.diff_add{background:#dfd} .diff_chg{background:#ffd} .diff_sub{background:#fdd}
.cols{display:flex;gap:1rem} .col{flex:1;border:1px solid #ddd;padding:.5rem}
pre{white-space:pre-wrap;margin:0}
.holdout{background:#f4f4f4;padding:.4rem;border-radius:4px}
""".strip()


@dataclass
class ReviewResponse:
    """One blinded response column — outcomes only, no identity."""

    diff: str
    holdout_results: list = field(default_factory=list)


@dataclass
class ReviewPacketItem:
    """A blinded comparison for review: prompt + two responses, no arm labels."""

    comparison_id: str
    task_prompt: str
    response1: ReviewResponse
    response2: ReviewResponse


def _diff_table(a: str, b: str) -> str:
    differ = difflib.HtmlDiff(wrapcolumn=72)
    # make_table escapes content itself; labels are neutral (no arm identity)
    return differ.make_table(
        a.splitlines(), b.splitlines(), "Response 1", "Response 2", context=True, numlines=2
    )


def build_review_packet(
    items: list[ReviewPacketItem], *, canaries: list[str] | None = None
) -> str:
    """Render the offline HTML packet from already-ordered, blinded ``items``.

    ``canaries`` are the per-experiment identity literals (arm names, model ids)
    to scrub in addition to the shared identity corpus. Items arrive in review
    order (disagreements first); the packet does not disclose that ordering's
    reason.
    """
    sections: list[str] = []
    for item in items:
        prompt = html.escape(blind_scrub(item.task_prompt, canaries))
        d1 = blind_scrub(item.response1.diff, canaries)
        d2 = blind_scrub(item.response2.diff, canaries)
        # scrub holdout result payloads too (a path/name could carry identity)
        h1 = blind_scrub(json.dumps(item.response1.holdout_results, sort_keys=True), canaries)
        h2 = blind_scrub(json.dumps(item.response2.holdout_results, sort_keys=True), canaries)
        table = blind_scrub(_diff_table(d1, d2), canaries)
        sections.append(
            f"<section><h2>Comparison {html.escape(item.comparison_id)}</h2>"
            f"<div class='task'><b>Task</b><pre>{prompt}</pre></div>"
            f"{table}"
            f"<div class='cols'>"
            f"<div class='col'><b>Response 1 holdouts</b>{_holdout_html_raw(h1)}</div>"
            f"<div class='col'><b>Response 2 holdouts</b>{_holdout_html_raw(h2)}</div>"
            f"</div></section>"
        )

    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Review packet</title><style>{_STYLE}</style></head>"
        "<body><h1>Human review packet</h1>"
        "<p>Record your verdict and the two integrity questions before any "
        "unblinding. This packet contains no judge opinion and no arm identities.</p>"
        + "".join(sections)
        + "</body></html>"
    )
    # Fail closed: no identity canary may survive into a shipped packet [AC-1].
    assert_identity_free(doc, canaries)
    return doc


def _holdout_html_raw(scrubbed_json: str) -> str:
    return "<div class='holdout'><pre>" + html.escape(scrubbed_json) + "</pre></div>"
