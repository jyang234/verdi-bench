"""The comparison dossier — one self-contained, three-layer HTML artifact
[EVAL-12 AC-3..AC-7, D002, D003].

A sibling renderer to the markdown/HTML renders behind the same fence:
``render_dossier`` delegates validation to :func:`findings.fence.validate_for_render`
— the one shared check list every renderer consumes — so every current and
future fence check applies identically and a refusing ledger raises the same
``AnalyzeError`` → the same ``cant_analyze`` reason [AC-4].

Three layers, one artifact:

* **verdict** (wide audience) — every sentence is generated from the
  :data:`VERDICT_TEMPLATES` registry and interpolates only [computed] findings
  fields; no sentence exists that does not map to a findings field [AC-5, D003];
* **analyst** — per-task paired deltas side-by-side, inline-SVG charts,
  per-trial trajectory timelines, judge calibration, confounds [AC-6];
* **auditor** — provenance, ledger head, chain-verify status, selfcheck,
  trajectory coverage [AC-6].

Self-containment is a determinism *and* leakage property [AC-3]: no network
references, no external assets, no scripts — collapse uses native
``<details>`` (zero JS, within D002's envelope) — and the render is a pure
function of ``(findings, ledger)``: sorted iteration, no clock, no RNG.
"""

from __future__ import annotations

import html as _html
from typing import Literal, Optional

from jinja2 import Environment, DictLoader
from markupsafe import Markup

from .findings.extract import paired_task_rows
from .findings.fence import validate_for_render
from .findings.model import ComparisonFinding, FindingsDocument, display_mde
from .findings.sections import (
    _WATERMARK,
    _fmt,
    _forensics_lines,
    _integrity_line,
    _judge_calibration_lines,
    _judge_coverage_lines,
    _ledger_consistency_lines,
    _override_lines,
    _process_lines,
    _provenance_lines,
    _secondary_lines,
    _tier_lines,
)
from .timeline import trial_timeline

NOT_MEASURED = "not measured"

# --- verdict layer: the computed-only sentence registry [AC-5, D003] ---------
# Every verdict-layer sentence comes from this registry; each template may
# interpolate ONLY the [computed] findings-derived fields in
# VERDICT_ALLOWED_FIELDS — the template-inventory test enforces the subset, so
# a narrative sentence that maps to no findings field is unrepresentable.
VERDICT_TEMPLATES: dict[str, str] = {
    "question": (
        "Pre-registered question: does {{ arm_a }} beat {{ arm_b }} "
        "on {{ primary_metric }}?"
    ),
    "rule": "Decision rule (verbatim): {{ decision_rule }}",
    "outcome_met": (
        "Outcome: an effect was detected and the pre-registered decision rule "
        "is MET — {{ arm_a }} is favored over {{ arm_b }} on "
        "{{ primary_metric }} (observed paired delta {{ observed_delta }})."
    ),
    "outcome_detected_not_met": (
        "Outcome: an effect was detected (observed paired delta "
        "{{ observed_delta }}), but the pre-registered decision rule is NOT met."
    ),
    # the pre-registered null phrasing — never "no difference" [AC-5]
    "outcome_null": "Outcome: No effect ≥ MDE detected (MDE={{ mde_value }}).",
    # F-H7: below the cluster floor there is no detection at all — structurally
    # insufficient, never phrased as a null result.
    "outcome_insufficient_clusters": (
        "Outcome: insufficient task clusters for any detection "
        "(N={{ n_tasks }} paired task(s)); no decision possible."
    ),
    "outcome_no_comparison": (
        "Outcome: no official comparison for {{ arm_a }} vs {{ arm_b }} — "
        "{{ exclusion_reason }}."
    ),
    "uncertainty": (
        "Uncertainty: {{ ci_level_pct }}% CI ({{ ci_method }}, {{ n_boot }} "
        "resamples) [{{ ci_low }}, {{ ci_high }}]; MDE={{ mde_value }}; "
        "N={{ n_tasks }} paired task(s)."
    ),
    "uncertainty_no_data": (
        "Uncertainty: no confidence interval (N={{ n_tasks }} paired task(s)); "
        "MDE={{ mde_value }}."
    ),
    "caveat_underpowered": (
        "Caveat: the design was ledgered as underpowered at lock "
        "(acknowledged; MDE={{ mde_value }})."
    ),
    "caveat_assumption_mde": (
        "Caveat: the MDE is assumption-based — variance not yet calibrated."
    ),
    # F-H6: under Holm the decision and the displayed interval use different
    # procedures; the verdict layer says so instead of implying one estimator.
    "caveat_holm": (
        "Caveat: this decision is Holm-Bonferroni-adjusted (recentered-bootstrap "
        "p-value); the interval above remains the unadjusted per-comparison CI."
    ),
}

# The closed set of [computed] fields verdict sentences may interpolate; every
# value is derived from FindingsDocument fields, formatted, nothing else.
VERDICT_ALLOWED_FIELDS = frozenset(
    {
        "arm_a",
        "arm_b",
        "primary_metric",
        "decision_rule",
        "observed_delta",
        "mde_value",
        "ci_low",
        "ci_high",
        "ci_level_pct",
        "ci_method",
        "n_boot",
        "n_tasks",
        "exclusion_reason",
    }
)

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Comparison dossier — {{ experiment_id }}</title>
<style>
body{font-family:system-ui,sans-serif;margin:1.5em;max-width:60em}
.watermark{background:#fee;color:#900;padding:4px 8px;font-weight:bold;border:1px solid #900;margin:6px 0}
.advisory{background:#ffd;color:#640;padding:4px 8px;border:1px solid #a80;margin:6px 0}
.layer{border:1px solid #ccc;border-radius:6px;padding:0.5em 1em;margin:1em 0}
table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:2px 8px;text-align:right}
th:first-child,td:first-child{text-align:left}
summary{font-weight:bold;cursor:pointer;margin:0.4em 0}
.nm{color:#666;font-style:italic}
svg{display:block;margin:4px 0}
</style>
</head>
<body>
<h1>Comparison dossier — {{ experiment_id }}</h1>
<p>mode: <strong>{{ mode }}</strong></p>
{% for layer in layers %}
<section class="layer" id="layer-{{ layer.id }}">
{% if watermark %}<div class="watermark">{{ watermark }}</div>{% endif %}
{% for banner in banners %}<div class="advisory">{{ banner }}</div>{% endfor %}
<h2>{{ layer.title }}</h2>
{% for sec in layer.sections %}
{% if watermark %}<div class="watermark">{{ watermark }}</div>{% endif %}
<details open>
<summary>{{ sec.title }}</summary>
{{ sec.body }}
</details>
{% endfor %}
</section>
{% endfor %}
</body>
</html>
"""

# Two environments on purpose: the page renders MARKUP (autoescaped), the
# verdict registry renders SENTENCES (plain text — escaping is the embedder's
# job, exactly once). Separate loaders also make a template-name collision
# with "page" unrepresentable.
_PAGE_ENV = Environment(
    loader=DictLoader({"page": _PAGE_TEMPLATE}),
    autoescape=True,
    keep_trailing_newline=True,
)
_VERDICT_ENV = Environment(loader=DictLoader(VERDICT_TEMPLATES), autoescape=False)


def _lines_html(lines: list[str]) -> Markup:
    """Markdown-ish disclosure lines → an escaped list — content parity with
    the markdown render, one source of truth for the wording."""
    items = "".join(f"<li>{_html.escape(line)}</li>" for line in lines)
    return Markup(f"<ul>{items}</ul>")


def _verdict_context(findings: FindingsDocument, cf: ComparisonFinding) -> dict:
    s = cf.stats or {}
    return {
        "arm_a": cf.arm_a,
        "arm_b": cf.arm_b,
        "primary_metric": findings.primary_metric,
        "decision_rule": findings.decision_rule,
        "observed_delta": _fmt(cf.decision.get("observed_delta")),
        "mde_value": _fmt(display_mde(findings.mde)),  # realized-N honest [F-M-S3]
        "ci_low": _fmt(s.get("ci_low")),
        "ci_high": _fmt(s.get("ci_high")),
        "ci_level_pct": int(s["ci_level"] * 100) if "ci_level" in s else None,
        "ci_method": s.get("ci_method"),
        "n_boot": s.get("n_boot"),
        "n_tasks": cf.n_tasks,
        "exclusion_reason": cf.exclusion_reason,
    }


def verdict_sentences(findings: FindingsDocument, cf: ComparisonFinding) -> list[str]:
    """The verdict layer for one comparison: an ordered selection from
    :data:`VERDICT_TEMPLATES`, selected by computed decision fields only.
    Uncertainty (CI/MDE/N) is always present, whichever branch renders [AC-5].
    """
    names = ["question", "rule"]
    if not cf.stats:
        names += ["outcome_no_comparison", "uncertainty_no_data"]
    elif cf.decision.get("floor") == "insufficient_clusters":
        names += ["outcome_insufficient_clusters", "uncertainty"]
    elif cf.decision.get("detected"):
        names.append(
            "outcome_met" if cf.decision.get("decides_positive") else "outcome_detected_not_met"
        )
        names.append("uncertainty")
    else:
        names += ["outcome_null", "uncertainty"]
    if cf.stats and cf.decision.get("correction") == "holm":
        names.append("caveat_holm")
    if findings.mde.acknowledged_underpowered:
        names.append("caveat_underpowered")
    if findings.mde.assumption_based_mde:
        names.append("caveat_assumption_mde")
    ctx = _verdict_context(findings, cf)
    return [_VERDICT_ENV.get_template(name).render(**ctx) for name in names]


# --- inline SVG (generated at render time from findings fields) [AC-3] -------
def _delta_svg(rows: list[dict], arm_a: str, arm_b: str) -> Markup:
    """Horizontal per-task delta bars; fixed-precision coordinates so the
    artifact is byte-deterministic."""
    if not rows:
        return Markup("")
    label_w, bar_w, row_h = 140.0, 260.0, 18.0
    height = row_h * len(rows) + 20.0
    max_abs = max(abs(r["delta"]) for r in rows) or 1.0
    mid = label_w + bar_w / 2.0
    parts = [
        f'<svg role="img" width="{label_w + bar_w:.0f}" height="{height:.0f}" '
        f'aria-label="per-task delta, {_html.escape(arm_a)} minus {_html.escape(arm_b)}">'
    ]
    parts.append(
        f'<line x1="{mid:.2f}" y1="0" x2="{mid:.2f}" y2="{height:.2f}" stroke="#999"/>'
    )
    for i, r in enumerate(rows):
        y = 10.0 + i * row_h
        w = (abs(r["delta"]) / max_abs) * (bar_w / 2.0)
        x = mid - w if r["delta"] < 0 else mid
        fill = "#a33" if r["delta"] < 0 else "#284"
        parts.append(
            f'<text x="0" y="{y + 10:.2f}" font-size="11">{_html.escape(r["task_id"])}</text>'
        )
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="12" fill="{fill}"/>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


_KIND_FILL = {
    "tool_call": "#579",
    "file_edit": "#284",
    "test_run": "#a60",
    "message": "#999",
}


def _steps_svg(steps: list[dict]) -> Markup:
    """One trial's step strip. Steps position by measured ``relative_ts`` when
    every step has one; otherwise by sequence index — order shown, timings
    honestly absent (the caption says so; nothing is estimated) [AC-6]."""
    if not steps:
        return Markup('<span class="nm">empty trajectory (0 steps)</span>')
    width, h = 400.0, 14.0
    timed = all(s.get("relative_ts") is not None for s in steps)
    max_ts = max((s.get("relative_ts") or 0.0) for s in steps) if timed else 0.0
    parts = [f'<svg role="img" width="{width:.0f}" height="{h:.0f}" aria-label="trial steps">']
    for i, s in enumerate(steps):
        if timed and max_ts > 0:
            x = (s["relative_ts"] / max_ts) * (width - 10.0)
        else:
            x = (i / max(len(steps) - 1, 1)) * (width - 10.0)
        fill = _KIND_FILL.get(s.get("kind"), "#000")
        parts.append(
            f'<rect x="{x:.2f}" y="2" width="8" height="10" fill="{fill}">'
            f"<title>{_html.escape(str(s.get('kind')))}</title></rect>"
        )
    parts.append("</svg>")
    caption = "" if timed else f'<span class="nm">step timings: {NOT_MEASURED} (sequence order only)</span>'
    return Markup("".join(parts) + caption)


# --- layer section builders ---------------------------------------------------
def _verdict_body(findings: FindingsDocument) -> Markup:
    blocks = []
    for cf in findings.comparisons:
        sentences = "".join(
            f"<p>{_html.escape(s)}</p>" for s in verdict_sentences(findings, cf)
        )
        blocks.append(f'<div class="comparison"><h4>{_html.escape(cf.label)}</h4>{sentences}</div>')
    return Markup("".join(blocks))


def _paired_delta_body(findings: FindingsDocument, ledger_path) -> Markup:
    blocks = []
    for cf in findings.comparisons:
        rows = paired_task_rows(ledger_path, findings.primary_metric, cf.arm_a, cf.arm_b)
        head = (
            f"<tr><th>task</th><th>{_html.escape(cf.arm_a)} (A)</th>"
            f"<th>{_html.escape(cf.arm_b)} (B)</th><th>delta</th></tr>"
        )
        body = "".join(
            f'<tr><td>{_html.escape(r["task_id"])}</td><td>{_fmt(r["a"])}</td>'
            f'<td>{_fmt(r["b"])}</td><td>{_fmt(r["delta"])}</td></tr>'
            for r in rows
        )
        table = f"<table>{head}{body}</table>" if rows else "<p>No paired task data.</p>"
        blocks.append(
            f"<h4>{_html.escape(cf.label)}</h4>{table}"
            f"{_delta_svg(rows, cf.arm_a, cf.arm_b)}"
        )
    return Markup("".join(blocks))


def _wall_time_html(row: dict) -> str:
    if row["wall_time_s"] is None:
        return f'<span class="nm">wall time: {NOT_MEASURED}</span>'
    return f"wall time: {_fmt(row['wall_time_s'], 2)}s"


def _timeline_body(timelines: dict) -> Markup:
    """Both arms' trials for a task, side by side in one view [AC-6]."""
    if not timelines:
        return Markup("<p>No trials recorded.</p>")
    blocks = []
    for task_id, arms in timelines.items():
        rows = []
        for arm, trials in arms.items():
            for t in trials:
                if t["steps"] is None:
                    strip = (
                        f'<span class="nm">trajectory: {_html.escape(t["trajectory_status"])}'
                        "</span>"
                    )
                else:
                    strip = str(_steps_svg(t["steps"]))
                nulls = t["telemetry_nulls"]
                nulls_html = (
                    f'<span class="nm">{NOT_MEASURED}: {_html.escape(", ".join(nulls))}</span>'
                    if nulls
                    else ""
                )
                rows.append(
                    "<tr>"
                    f"<td>{_html.escape(arm)}</td>"
                    f'<td>{_html.escape(t["trial_id"])} (rep {t["repetition"]})</td>'
                    f'<td>{_html.escape(str(t["outcome"]))}</td>'
                    f"<td>{_wall_time_html(t)} {nulls_html}</td>"
                    f"<td>{strip}</td>"
                    "</tr>"
                )
        blocks.append(
            f"<h4>task {_html.escape(task_id)}</h4>"
            "<table><tr><th>arm</th><th>trial</th><th>outcome</th>"
            f"<th>telemetry</th><th>trajectory</th></tr>{''.join(rows)}</table>"
        )
    return Markup("".join(blocks))


def _coverage_body(timelines: dict) -> Markup:
    counts: dict[str, int] = {}
    for arms in timelines.values():
        for trials in arms.values():
            for t in trials:
                counts[t["trajectory_status"]] = counts.get(t["trajectory_status"], 0) + 1
    lines = [f"{status}: {counts[status]} trial(s)" for status in sorted(counts)]
    return _lines_html(lines or ["no trials recorded"])


def _selfcheck_lines(ledger_path) -> list[str]:
    from .selfcheck import latest_selfcheck, selfcheck_status

    status = selfcheck_status(ledger_path)
    lines = [f"selfcheck status: {status}"]
    latest = latest_selfcheck(ledger_path)
    if latest is not None:
        lines.append(
            f"latest selfcheck: method={latest.get('selected_method')} "
            f"coverage={latest.get('coverage')} passed={latest.get('passed')}"
        )
    return lines


def _disclosure_sections(findings: FindingsDocument) -> list[dict]:
    """The disclosure blocks that carry over into EVERY layer [AC-4]."""
    sections = [
        {
            "title": "Confounds (disclosed, non-suppressing)",
            "body": _lines_html([c["flag"] for c in findings.confounds] or ["none"]),
        },
        {
            "title": "Blinding integrity",
            "body": _lines_html([_integrity_line(findings)]),
        },
    ]
    # NB: grade-tier lines are NOT a section here — they ride every layer as
    # the ADVISORY banner (render_dossier), and a second copy per layer would
    # just be the same sentences twice.
    for title, lines in (
        ("Ledger consistency", _ledger_consistency_lines(findings)),
        ("Terminal overrides", _override_lines(findings)),
        ("Judge coverage", _judge_coverage_lines(findings)),
    ):
        if lines:
            sections.append({"title": title, "body": _lines_html(lines)})
    if not findings.rubric_committed:
        sections.append(
            {
                "title": "Rubric commitment",
                "body": _lines_html(
                    [
                        "⚠ CAVEAT: this experiment was locked before rubric "
                        "commitment (D-P7-6); the judging rubric content is not "
                        "pinned, so a post-lock rubric change cannot be detected "
                        "from the ledger"
                    ]
                ),
            }
        )
    if findings.process is not None:
        sections.append(
            {
                "title": f"Process diagnostics — {_WATERMARK} (advisory secondary)",
                "body": _lines_html(_process_lines(findings)),
            }
        )
    if findings.forensics is not None:
        # EVAL-11 AC-5: one addition here rides every layer — same wording as
        # the markdown render, disclosure-only [D004]
        sections.append(
            {
                "title": "Forensic flags (disclosed, non-suppressing)",
                "body": _lines_html(_forensics_lines(findings)),
            }
        )
    return sections


def render_dossier(
    findings: FindingsDocument,
    ledger_path,
    mode: Literal["official", "exploratory"] = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Render the three-layer dossier behind the markdown render's exact fence.

    Fence parity by shared validation [AC-4]: the dossier runs the SAME
    :func:`~harness.analyze.findings.fence.validate_for_render` the markdown
    render runs — provenance, process disclosure, head-hash/chain verify, and
    (official) the metric gate + the calibration fence — so it is refused
    precisely when the markdown is, with the same ``AnalyzeError`` subtype and
    therefore the same ``cant_analyze`` reason. No full markdown render is built
    and discarded just for the side effects [refactor 07 §1].
    """
    validate_for_render(findings, ledger_path, mode, metric=metric, corpus_manifest=corpus_manifest)

    timelines = trial_timeline(ledger_path)
    disclosures = _disclosure_sections(findings)

    verdict_sections = [{"title": "Verdict", "body": _verdict_body(findings)}] + disclosures
    analyst_sections = [
        {"title": "Per-task paired deltas (A vs B)", "body": _paired_delta_body(findings, ledger_path)},
        {"title": "Per-trial trajectory timelines", "body": _timeline_body(timelines)},
        {"title": "Secondary metrics (exploratory)", "body": _lines_html(_secondary_lines(findings))},
    ]
    if findings.judge_calibration is not None:
        analyst_sections.append(
            {"title": "Judge calibration (per class)", "body": _lines_html(_judge_calibration_lines(findings))}
        )
    analyst_sections += disclosures
    auditor_sections = [
        {"title": "Provenance", "body": _lines_html(_provenance_lines(findings))},
        {
            "title": "Chain verification",
            "body": _lines_html(
                [
                    f"ledger head: {findings.provenance.ledger_head_hash}",
                    f"chain_ok={findings.provenance.chain_ok} (verify_chain at render time)",
                ]
            ),
        },
        {"title": "Coverage selfcheck (D008)", "body": _lines_html(_selfcheck_lines(ledger_path))},
        {"title": "Trajectory coverage", "body": _coverage_body(timelines)},
        {
            "title": "CI method selection (coverage)",
            "body": _lines_html([f"selected method: {findings.ci_selection.get('selected_method')}"]),
        },
    ] + disclosures

    layers = [
        {"id": "verdict", "title": "Verdict — the pre-registered answer", "sections": verdict_sections},
        {"id": "analyst", "title": "Analyst — how the arms behaved", "sections": analyst_sections},
        {"id": "auditor", "title": "Auditor — verify it yourself", "sections": auditor_sections},
    ]
    banners = _tier_lines(findings)
    return _PAGE_ENV.get_template("page").render(
        experiment_id=findings.experiment_id,
        mode=mode,
        watermark=_WATERMARK if mode != "official" else None,
        banners=banners,
        layers=layers,
    )
