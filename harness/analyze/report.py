"""Findings computation + the pre-registration fence [EVAL-6 §M4].

``compute_findings`` is the pure core: a reproducible function of
``(ledger, spec, seed, corpus_manifest)`` producing a :class:`FindingsDocument`.
``render_findings`` turns it into an official or exploratory render, and is where
the fence is mechanical:

* **official** renders *only* the pre-registered primary metric + decision rule;
  asking for official on anything unregistered is refused [AC-5], and official is
  refused unless the corpus is ``full-run-validated`` [EVAL-8 AC-2 hook];
* **everything else** carries an EXPLORATORY watermark on every section, with
  secondaries always labeled exploratory [AC-5, D003];
* MDE appears in every render; a null is phrased "no effect ≥ MDE detected"
  [AC-3]; ``acknowledged_underpowered`` is surfaced when ledgered;
* the provenance block is schema-required (a missing field fails validation),
  and the ledger head hash is cross-checked against ``verify_chain`` at render
  time [AC-6];
* cross-stack comparisons run only over telemetry both arms measured — a metric
  with asymmetric nulls is excluded and flagged, never imputed [AC-7]; raw token
  counts never cross vendors [EVAL-6 constraint].
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from ..ledger import events
from ..ledger.query import find_events, ledger_head_hash, verify
from ..schema.metrics import PrimaryMetric
from ..version import instrument_identity
from .confounds import asymmetric_null_fields, flag_confounds
from .effect import effect_sizes
from .nullsim import VarianceParams, select_ci_method
from .stats import BootstrapResult, paired_bootstrap

# Telemetry-derived primary metrics and the field each reads.
_METRIC_TELEMETRY_FIELD = {
    "cost_per_task": "cost",
    "wall_time": "wall_time_s",
}
# Raw token fields are never compared across vendors [EVAL-6 constraint].
_RAW_TOKEN_FIELDS = ("tokens_in", "tokens_out", "tokens_cache")
# Cross-vendor comparisons are restricted to these dimensions.
_CROSS_VENDOR_ALLOWED = ("cost", "wall_time_s", "tool_calls")


class AnalyzeError(RuntimeError):
    """Base for analyze-stage failures."""


class UnregisteredOfficialError(AnalyzeError):
    """Official render requested for a non-pre-registered metric [AC-5]."""


class CalibrationIncompleteError(AnalyzeError):
    """Official render requested before the corpus is full-run-validated."""


class ProvenanceError(AnalyzeError):
    """A finding is missing provenance, or the head hash no longer verifies."""


# --- schema ----------------------------------------------------------------
class Provenance(BaseModel):
    # every field required ⇒ a render missing any provenance fails validation [AC-6]
    model_config = ConfigDict(extra="forbid")
    instrument_version: str
    instrument_git_sha: str
    corpus: Optional[dict]
    ledger_head_hash: str
    chain_ok: bool
    judge: dict


class ComparisonFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    arm_a: str
    arm_b: str
    n_tasks: int
    stats: dict
    effect: dict
    decision: dict
    excluded_from_official: bool = False
    exclusion_reason: Optional[str] = None


class MDEBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Optional[float]
    assumption_based_mde: bool
    acknowledged_underpowered: bool


class FindingsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    seed: int
    primary_metric: str
    decision_rule: str
    comparisons: list[ComparisonFinding]
    mde: MDEBlock
    ci_selection: dict
    confounds: list[dict]
    secondary_metrics: dict
    integrity: dict
    process: Optional[dict] = None
    provenance: Provenance


# --- metric extraction -----------------------------------------------------
def _trial_index(ledger_path) -> dict[str, dict]:
    """``trial_id -> {task_id, arm}`` from trial records."""
    out = {}
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        out[rec["trial_id"]] = rec
    return out


def _holdout_values(ledger_path) -> dict[str, dict[str, list[float]]]:
    """``task_id -> arm -> [binary pass (0/1) per trial]`` from grade events."""
    trials = _trial_index(ledger_path)
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in find_events(ledger_path, events.GRADE):
        rec = trials.get(ev["trial_id"])
        if rec is None:
            continue
        acc[rec["task_id"]][rec["arm"]].append(1.0 if ev["binary_score"] else 0.0)
    return acc


def _telemetry_values(ledger_path, field: str) -> dict[str, dict[str, list[float]]]:
    """``task_id -> arm -> [telemetry field per non-null trial]``."""
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        val = rec.get("telemetry", {}).get(field)
        if val is not None:
            acc[rec["task_id"]][rec["arm"]].append(float(val))
    return acc


def _judge_preference_values(ledger_path) -> list[float]:
    """Per-comparison preference in {+1 (A), -1 (B), 0 (tie/cant)}.

    Judge preference is already an A-vs-B quantity, so each comparison is its own
    cluster and the value is the delta directly.
    """
    out = []
    for ev in find_events(ledger_path, events.JUDGE_VERDICT):
        w = ev["verdict"]["winner"]
        out.append(1.0 if w == "A" else -1.0 if w == "B" else 0.0)
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _paired_arm_series(
    per_task: dict[str, dict[str, list[float]]], arm_a: str, arm_b: str
) -> tuple[list[float], list[float]]:
    """Reduce over reps and pair on tasks present in *both* arms (sorted)."""
    a_vals, b_vals = [], []
    for task_id in sorted(per_task):
        arms = per_task[task_id]
        if arm_a in arms and arm_b in arms and arms[arm_a] and arms[arm_b]:
            a_vals.append(_mean(arms[arm_a]))
            b_vals.append(_mean(arms[arm_b]))
    return a_vals, b_vals


# --- findings computation --------------------------------------------------
def _lock_event(ledger_path) -> dict:
    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    if not locks:
        raise AnalyzeError("no experiment_locked event; run `bench plan` first")
    return locks[0]


def _mde_block(ledger_path) -> MDEBlock:
    lock = _lock_event(ledger_path)
    mde = lock.get("mde", {})
    ack = bool(find_events(ledger_path, events.ACKNOWLEDGED_UNDERPOWERED))
    return MDEBlock(
        value=mde.get("mde"),
        assumption_based_mde="assumption_based_mde" in mde.get("flags", []),
        acknowledged_underpowered=ack,
    )


def _variance_params(ledger_path) -> VarianceParams:
    mde = _lock_event(ledger_path).get("mde", {})
    return VarianceParams(
        p=float(mde.get("p", 0.5)),
        rho=float(mde.get("rho", 0.3)),
        n_tasks=int(mde.get("n_tasks", 50)),
    )


def _judge_summary(ledger_path) -> dict:
    verdicts = find_events(ledger_path, events.JUDGE_VERDICT)
    models = sorted({v["verdict"]["provenance"]["judge_model"] for v in verdicts})
    rubrics = sorted({v["verdict"]["provenance"]["rubric_sha256"] for v in verdicts})
    return {"judge_models": models, "rubric_shas": rubrics, "n_verdicts": len(verdicts)}


def _integrity(ledger_path) -> dict:
    """Blinding-integrity rate — rides every render [EVAL-7 AC-6].

    Computed from human verdicts' integrity fields; ``None`` rate until human
    review exists, but the field is always present so a render can never omit it.
    """
    recognized, guessed_right, n = 0, 0, 0
    for ev in find_events(ledger_path, events.HUMAN_VERDICT):
        v = ev["verdict"]
        if "arm_recognized" not in v:
            continue
        n += 1
        if v.get("arm_recognized"):
            recognized += 1
            if v.get("arm_guess") and v.get("arm_guess") == v.get("actual_arm"):
                guessed_right += 1
    rate = recognized / n if n else None
    guess_acc = guessed_right / recognized if recognized else None
    return {"rate": rate, "n_reviews": n, "recognized": recognized, "guess_accuracy": guess_acc}


def _secondary_metrics(ledger_path, spec) -> dict:
    """Exploratory per-arm telemetry means, with cross-vendor token honesty.

    Raw token fields are excluded from cross-vendor comparison; when the two arms
    are different vendors, token fields are marked vendor-incomparable [constraint].
    """
    from .confounds import _vendor

    arm_vendor = {a.name: _vendor(a.model) for a in spec.arms}
    cross_vendor = len(set(arm_vendor.values())) > 1
    fields = ("tokens_in", "tokens_out", "tokens_cache", "cost", "wall_time_s", "tool_calls")
    per_arm: dict[str, dict[str, float]] = defaultdict(dict)
    raw: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        for f in fields:
            val = rec.get("telemetry", {}).get(f)
            if val is not None:
                raw[rec["arm"]][f].append(float(val))
    for arm, fvals in raw.items():
        for f, xs in fvals.items():
            per_arm[arm][f] = _mean(xs)
    vendor_incomparable = [f for f in _RAW_TOKEN_FIELDS] if cross_vendor else []
    return {
        "exploratory": True,
        "per_arm_means": {a: dict(sorted(v.items())) for a, v in sorted(per_arm.items())},
        "cross_vendor": cross_vendor,
        "vendor_incomparable_fields": vendor_incomparable,
        "cross_vendor_allowed_fields": list(_CROSS_VENDOR_ALLOWED),
    }


def compute_findings(
    ledger_path,
    spec,
    seed: int,
    *,
    corpus_manifest=None,
    coverage_n_sim: int = 200,
    coverage_n_boot: int = 500,
    n_boot: int = 10_000,
) -> FindingsDocument:
    """Compute the findings document — pure and reproducible in ``seed``."""
    primary = spec.primary_metric.value
    params = _variance_params(ledger_path)
    selection = select_ci_method(params, seed, n_sim=coverage_n_sim, n_boot=coverage_n_boot)
    ci_method = selection.selected_method

    # metric → per-task per-arm value series
    if primary == PrimaryMetric.holdout_pass_rate.value:
        per_task = _holdout_values(ledger_path)
        metric_field = None
    elif primary in _METRIC_TELEMETRY_FIELD:
        metric_field = _METRIC_TELEMETRY_FIELD[primary]
        per_task = _telemetry_values(ledger_path, metric_field)
    elif primary == PrimaryMetric.judge_preference.value:
        per_task = None
        metric_field = None
    else:  # pragma: no cover - enum is closed
        raise AnalyzeError(f"unsupported primary metric {primary!r}")

    excluded_fields = set(asymmetric_null_fields(ledger_path))
    parsed_rule = spec.parsed_rule

    comparisons: list[ComparisonFinding] = []
    arm_a = spec.arms[0].name
    for other in spec.arms[1:]:
        arm_b = other.name
        if primary == PrimaryMetric.judge_preference.value:
            deltas = _judge_preference_values(ledger_path)
            a_vals = [max(d, 0.0) for d in deltas]
            b_vals = [max(-d, 0.0) for d in deltas]
        else:
            a_vals, b_vals = _paired_arm_series(per_task, arm_a, arm_b)
            deltas = [a - b for a, b in zip(a_vals, b_vals)]

        excluded = metric_field is not None and metric_field in excluded_fields
        if excluded:
            # Asymmetric nulls ⇒ the metric is excluded from official comparison
            # and flagged, never imputed [AC-7] — regardless of any partial data.
            comparisons.append(
                ComparisonFinding(
                    label=f"{arm_a} vs {arm_b}", arm_a=arm_a, arm_b=arm_b,
                    n_tasks=len(deltas), stats={}, effect={},
                    decision={"rule": parsed_rule.raw, "observed_delta": None,
                              "decides_positive": None},
                    excluded_from_official=True,
                    exclusion_reason=(
                        f"telemetry field {metric_field!r} has asymmetric nulls; "
                        "excluded from official comparison, never imputed [AC-7]"
                    ),
                )
            )
            continue
        if not deltas:
            # no paired data — record an explicit empty finding rather than crash
            comparisons.append(
                ComparisonFinding(
                    label=f"{arm_a} vs {arm_b}", arm_a=arm_a, arm_b=arm_b, n_tasks=0,
                    stats={}, effect={},
                    decision={"rule": parsed_rule.raw, "observed_delta": None,
                              "decides_positive": None},
                    excluded_from_official=True,
                    exclusion_reason="no paired task data",
                )
            )
            continue

        boot: BootstrapResult = paired_bootstrap(deltas, seed, ci_method, n_boot=n_boot)
        eff = effect_sizes(a_vals, b_vals)
        observed = eff.mean_paired_delta
        comparisons.append(
            ComparisonFinding(
                label=f"{arm_a} vs {arm_b}",
                arm_a=arm_a,
                arm_b=arm_b,
                n_tasks=boot.n_tasks,
                stats=boot.as_dict(),
                effect=eff.as_dict(),
                decision={
                    "rule": parsed_rule.raw,
                    "observed_delta": observed,
                    "decides_positive": parsed_rule.decides_positive(observed),
                },
                excluded_from_official=excluded,
                exclusion_reason=(
                    f"telemetry field {metric_field!r} has asymmetric nulls; "
                    "excluded from official comparison, never imputed [AC-7]"
                    if excluded
                    else None
                ),
            )
        )

    corpus_prov = corpus_manifest.provenance_ref() if corpus_manifest is not None else None
    chain_result = verify(ledger_path)
    ident = instrument_identity()
    provenance = Provenance(
        instrument_version=ident["version"],
        instrument_git_sha=ident["git_sha"],
        corpus=corpus_prov,
        ledger_head_hash=ledger_head_hash(ledger_path),
        chain_ok=chain_result.ok,
        judge=_judge_summary(ledger_path),
    )

    return FindingsDocument(
        experiment_id=find_events(ledger_path, events.EXPERIMENT_LOCKED)[0]["provenance"][
            "experiment_id"
        ],
        seed=seed,
        primary_metric=primary,
        decision_rule=parsed_rule.raw,
        comparisons=comparisons,
        mde=_mde_block(ledger_path),
        ci_selection=selection.as_dict(),
        confounds=flag_confounds(ledger_path, spec),
        secondary_metrics=_secondary_metrics(ledger_path, spec),
        integrity=_integrity(ledger_path),
        provenance=provenance,
    )


# --- rendering + the fence -------------------------------------------------
def _fmt(x: Optional[float], dp: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{dp}f}"


def _validate_provenance(findings: FindingsDocument) -> None:
    p = findings.provenance
    for name in ("instrument_version", "instrument_git_sha", "ledger_head_hash", "judge"):
        if getattr(p, name) in (None, ""):
            raise ProvenanceError(f"findings provenance missing {name} [AC-6]")


def _assert_head_hash(findings: FindingsDocument, ledger_path) -> None:
    """Cross-check the recorded head hash against verify_chain at render time [AC-6]."""
    result = verify(ledger_path)
    if not result.ok:
        raise ProvenanceError(f"ledger chain does not verify at render: {result.detail}")
    current = ledger_head_hash(ledger_path)
    if current != findings.provenance.ledger_head_hash:
        raise ProvenanceError(
            "ledger head hash changed since the findings were computed "
            f"(recorded {findings.provenance.ledger_head_hash[:12]}…, "
            f"now {current[:12]}…) — findings are stale [AC-6]"
        )


def _comparison_lines(cf: ComparisonFinding, mde: MDEBlock) -> list[str]:
    lines = [f"**Comparison: {cf.label}**  (n_tasks={cf.n_tasks})"]
    if not cf.stats:
        lines.append(f"- No paired task data ({cf.exclusion_reason}).")
        return lines
    s = cf.stats
    ci = f"[{_fmt(s['ci_low'])}, {_fmt(s['ci_high'])}]"
    detected = s["ci_low"] > 0.0 or s["ci_high"] < 0.0
    lines.append(f"- mean paired delta: {_fmt(cf.effect['mean_paired_delta'])}")
    lines.append(f"- Cliff's delta: {_fmt(cf.effect['cliffs_delta'])}")
    lines.append(
        f"- {int(s['ci_level'] * 100)}% CI ({s['ci_method']}, {s['n_boot']} resamples): {ci}"
    )
    mde_val = _fmt(mde.value)
    if detected:
        decides = cf.decision["decides_positive"]
        lines.append(
            f"- Effect detected. Decision rule `{cf.decision['rule']}` ⇒ "
            f"{'MET' if decides else 'not met'}."
        )
    else:
        # structural null phrasing [AC-3, D003]
        lines.append(f"- No effect ≥ MDE detected (MDE={mde_val}).")
    if cf.excluded_from_official:
        lines.append(f"- ⚠ EXCLUDED from official comparison: {cf.exclusion_reason}")
    return lines


def _mde_lines(mde: MDEBlock) -> list[str]:
    lines = [f"MDE = {_fmt(mde.value)}"]
    if mde.assumption_based_mde:
        lines.append("  (assumption_based_mde: variance not yet calibrated)")
    if mde.acknowledged_underpowered:
        lines.append("  (acknowledged_underpowered: design ledgered as underpowered)")
    return lines


def _provenance_lines(findings: FindingsDocument) -> list[str]:
    p = findings.provenance
    lines = [
        f"- instrument: {p.instrument_version} @ {p.instrument_git_sha[:12]}",
        f"- ledger head: {p.ledger_head_hash[:16]}…  chain_ok={p.chain_ok}",
        f"- judge: {p.judge}",
    ]
    if p.corpus is not None:
        lines.append(
            f"- corpus: {p.corpus['corpus_id']}@{p.corpus['semver']} "
            f"({p.corpus['calibration_status']}), {len(p.corpus['task_shas'])} task sha(s)"
        )
    else:
        lines.append("- corpus: (none provided)")
    return lines


def render_markdown(
    findings: FindingsDocument,
    ledger_path,
    mode: Literal["official", "exploratory"] = "exploratory",
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> str:
    """Render findings to markdown behind the pre-registration fence."""
    _validate_provenance(findings)
    _assert_head_hash(findings, ledger_path)

    if mode == "official":
        if metric is not None and metric != findings.primary_metric:
            raise UnregisteredOfficialError(
                f"official render requested for {metric!r}, but the pre-registered "
                f"primary metric is {findings.primary_metric!r}; only the "
                "primary metric + decision rule are official [AC-5]"
            )
        _assert_official_calibration(findings, corpus_manifest)
        return _render_official_md(findings)
    return _render_exploratory_md(findings)


def _assert_official_calibration(findings: FindingsDocument, corpus_manifest) -> None:
    """Official render requires a full-run-validated corpus [EVAL-8 AC-2 hook]."""
    corpus = findings.provenance.corpus
    status = None
    if corpus_manifest is not None:
        status = corpus_manifest.calibration.status
    elif corpus is not None:
        status = corpus.get("calibration_status")
    if status != "full-run-validated":
        raise CalibrationIncompleteError(
            "official findings require the corpus to be full-run-validated "
            f"(status={status!r}); calibrate before the first official finding "
            "[EVAL-8 AC-2]"
        )


def _render_official_md(findings: FindingsDocument) -> str:
    out = [
        f"# Official findings — {findings.experiment_id}",
        f"Pre-registered primary metric: **{findings.primary_metric}**",
        f"Decision rule: `{findings.decision_rule}`",
        "",
        "## Minimum detectable effect",
        *_mde_lines(findings.mde),
        "",
        "## Primary metric",
    ]
    for cf in findings.comparisons:
        if cf.excluded_from_official:
            out.append(
                f"### Comparison: {cf.label} — EXCLUDED ({cf.exclusion_reason})"
            )
            continue
        out.extend(_comparison_lines(cf, findings.mde))
    out += ["", "## Confounds (disclosed, non-suppressing)"]
    out += [f"- {c['flag']}" for c in findings.confounds] or ["- none"]
    out += ["", f"## Blinding integrity", f"- {_integrity_line(findings)}"]
    out += ["", "## Provenance", *_provenance_lines(findings)]
    out += ["", f"CI method selected by coverage: {findings.ci_selection['selected_method']}"]
    return "\n".join(out) + "\n"


_WATERMARK = "⚠ EXPLORATORY — not an official, pre-registered finding"


def _render_exploratory_md(findings: FindingsDocument) -> str:
    def section(title: str, body: list[str]) -> list[str]:
        # watermark on EVERY section header [AC-5, D003]
        return [f"## {_WATERMARK}", f"### {title}", *body, ""]

    out = [f"# Findings (EXPLORATORY) — {findings.experiment_id}", _WATERMARK, ""]
    out += section("Pre-registered context", [
        f"- primary metric: {findings.primary_metric}",
        f"- decision rule: `{findings.decision_rule}`",
    ])
    out += section("Minimum detectable effect", _mde_lines(findings.mde))
    for cf in findings.comparisons:
        out += section(f"Primary metric — {cf.label}", _comparison_lines(cf, findings.mde))
    out += section("Secondary metrics (exploratory)", _secondary_lines(findings))
    out += section("Confounds (disclosed, non-suppressing)",
                   [f"- {c['flag']}: {c}" for c in findings.confounds] or ["- none"])
    out += section("Blinding integrity", [f"- {_integrity_line(findings)}"])
    out += section("CI method selection (coverage)", [f"- {findings.ci_selection}"])
    out += section("Provenance", _provenance_lines(findings))
    return "\n".join(out) + "\n"


def _secondary_lines(findings: FindingsDocument) -> list[str]:
    sm = findings.secondary_metrics
    lines = [f"- per-arm means: {sm['per_arm_means']}"]
    if sm["cross_vendor"]:
        lines.append(
            f"- cross-vendor: raw token fields {sm['vendor_incomparable_fields']} are "
            "vendor-incomparable and NOT compared across arms; cross-vendor "
            f"comparisons restricted to {sm['cross_vendor_allowed_fields']}"
        )
    return lines


def _integrity_line(findings: FindingsDocument) -> str:
    i = findings.integrity
    if i["rate"] is None:
        return "blinding integrity: n/a (no human review recorded yet)"
    return (
        f"blinding integrity rate: {_fmt(i['rate'], 3)} over {i['n_reviews']} review(s); "
        f"guess accuracy: {_fmt(i['guess_accuracy'], 3)}"
    )


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
        body_lines.append(f"<p>{line}</p>")
    style = (
        "<style>.watermark{background:#fee;color:#900;padding:4px;"
        "font-weight:bold;border:1px solid #900;margin:6px 0}</style>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{style}</head><body>{''.join(body_lines)}</body></html>"
    )
