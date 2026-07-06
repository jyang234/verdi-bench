"""The benchmark result card — verdi's comparability & legibility layer.

A **read-only projection** of an already-analyzed run into a versioned, canonical
artifact that is (a) *citable* — tamper-evident provenance — and (b) *comparable*
against another run, where comparability is machine-verifiable, not asserted.
See ``docs/design/review/verdi-bench-result-card-design.md`` for the decisions.

Two cards are comparable iff they ran the same task set: the card carries a
``battery_sha`` derived from the tamper-evident task commitment
(``compute_commitment``'s ``task_shas_sha256``) or, with a corpus manifest, from
the corpus's *intrinsic* per-task shas (image-insensitive). :func:`compare_cards`
refuses across different batteries/metrics — a loud mismatch, never a silent one.

This module computes **no new statistic**: the paired delta/CI/decision come from
:func:`harness.analyze.report.compute_findings`, and the per-arm absolute score is
the mean :func:`harness.analyze.report.per_arm_absolute_scores` already exposes.
The card only *projects and formats*.
"""

from __future__ import annotations

import json
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict

from ..corpus.commit import content_sha
from ..ledger import events
from ..ledger.query import find_events, read_events, verify
from .findings.extract import compute_findings, per_arm_absolute_scores
from .findings.sections import asymmetry_line

CARD_SCHEMA_VERSION = 2


class CardError(RuntimeError):
    """A card cannot be built or two cards cannot be compared — stated with the
    reason. Fail loud [master plan §7.7]: a card that silently omits provenance
    or silently compares mismatched batteries would defeat its own purpose."""


class ResultCard(BaseModel):
    """The typed result card [refactor 07 §5] — mirrors the card dict EXACTLY.

    Top-level fields are typed; the section blocks stay dicts (the
    :class:`~harness.analyze.report.FindingsDocument` convention), so
    ``model_dump()`` IS today's dict and :func:`serialize_card` reproduces the
    golden-pinned bytes unchanged. Any field change is a ``CARD_SCHEMA_VERSION``
    bump + a comparability story — out of scope here [07 §5].

    Mapping-style reads (``card["battery"]``, ``card.get("comparison")``) are
    kept so a card and its re-loaded JSON dict expose one read interface —
    :func:`compare_cards` accepts either.
    """

    model_config = ConfigDict(extra="forbid")
    schema_version: int
    # {version, git_sha, tier} — tier is always ADVISORY [AN-11]
    instrument: dict
    # the comparability key + its basis [design §'battery_sha semantics']
    battery: dict
    primary_metric: str
    decision_rule: str
    # per-arm {name, model, aux_models, absolute_score, n} — the leaderboard
    # number; None score for a pairwise-only metric, never faked
    arms: list[dict]
    # the pre-registered primary pair's co-equal delta/CI/decision block;
    # None when the findings carry no comparison
    comparison: Optional[dict]
    provenance: dict
    disclosures: dict

    def __getitem__(self, key: str):
        if key not in type(self).model_fields:
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key: str, default=None):
        """Dict-parity read — the same ``.get`` a re-loaded card JSON offers."""
        if key not in type(self).model_fields:
            return default
        return getattr(self, key)


def _lock_event(ledger_path) -> dict:
    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    if not locks:
        raise CardError("no experiment_locked event: nothing to card")
    return locks[0]


def _rendered_event(ledger_path) -> dict:
    """The most recent findings render, verified fresh [F-H5].

    A card certifies what was actually rendered, so `bench analyze` must have
    run first — and nothing may have been appended since. Any post-render event
    (a quarantine, a re-grade, another verb) means the numbers the card would
    recompute match no fenced render; the honest answer is a refusal naming the
    remedy, not an "official"-stamped card over drifted data. Deliberately
    strict: there is no allowlist of "harmless" post-render event kinds — that
    taxonomy does not exist and would be a silent-failure surface."""
    rendered = find_events(ledger_path, events.FINDINGS_RENDERED)
    if not rendered:
        raise CardError(
            "no findings_rendered event: run `bench analyze` before emitting a card"
        )
    trailing = 0
    for ev in reversed(read_events(ledger_path)):
        if ev.get("event") == events.FINDINGS_RENDERED:
            break
        trailing += 1
    if trailing:
        raise CardError(
            f"{trailing} event(s) appended since the last findings render — the "
            "card certifies a rendered result; re-run `bench analyze` before "
            "emitting a card [F-H5]"
        )
    return rendered[-1]


def _battery(ledger_path, task_ids: list[str], corpus_manifest) -> dict:
    """The comparability key + its basis [design §'battery_sha semantics'].

    With a corpus manifest: the battery is the corpus's *intrinsic* per-task shas
    for the tasks that ran (image-insensitive for the SWE-bench importer). Without
    one: the lock's ``task_shas_sha256`` (image-sensitive, but always present and
    tamper-evident)."""
    lock = _lock_event(ledger_path)
    commitment = lock.get("task_commitment") or {}
    if corpus_manifest is not None:
        shas: dict[str, str] = {}
        for tid in sorted(task_ids):
            entry = corpus_manifest.task(tid)
            if entry is None:
                raise CardError(
                    f"corpus manifest does not cover task {tid!r}; it cannot anchor "
                    "an image-insensitive battery_sha for this run"
                )
            shas[tid] = entry.sha
        return {
            "battery_sha": content_sha(shas),
            "battery_basis": "corpus",
            "corpus_id": corpus_manifest.corpus_id,
            "semver": corpus_manifest.semver,
            "dataset": (
                {"name": corpus_manifest.dataset.name, "version": corpus_manifest.dataset.version}
                if corpus_manifest.dataset is not None else None
            ),
            "n_tasks": len(shas),
        }
    sha = commitment.get("task_shas_sha256")
    if not sha:
        raise CardError(
            "experiment_locked carries no task commitment; cannot anchor a "
            "battery_sha. Re-plan with tasks.yaml present, or pass --corpus."
        )
    return {
        "battery_sha": sha,
        "battery_basis": "lock_commitment",
        "corpus_id": commitment.get("corpus_id"),
        "semver": commitment.get("semver"),
        "dataset": None,
        "n_tasks": len(task_ids),
    }


def build_card(
    ledger_path,
    spec,
    *,
    task_ids: list[str],
    corpus_manifest=None,
) -> ResultCard:
    """Project a completed, analyzed run into a typed :class:`ResultCard` (pure).

    ``task_ids`` are the committed task ids (the CLI reads them from tasks.yaml).
    Requires a prior ``bench analyze`` (the card certifies a rendered result).
    """
    chain = verify(ledger_path)
    if not chain.ok:
        raise CardError(
            f"ledger chain does not verify at card emission: {chain.detail} [F-H5]"
        )
    rendered = _rendered_event(ledger_path)
    mode = rendered["mode"]
    lock = _lock_event(ledger_path)
    findings = compute_findings(ledger_path, spec, spec.seed, corpus_manifest=corpus_manifest)
    prov = findings.provenance
    primary = findings.primary_metric

    per_arm = per_arm_absolute_scores(ledger_path, primary, spec)
    arms = [
        {
            "name": arm.name,
            "model": arm.model,
            "aux_models": [a.model for a in arm.aux_models],
            "absolute_score": per_arm[arm.name]["score"],
            "n": per_arm[arm.name]["n"],
        }
        for arm in spec.arms
    ]

    # the pre-registered primary pair carries the co-equal comparison block.
    cf = findings.comparisons[0] if findings.comparisons else None
    comparison: Optional[dict] = None
    if cf is not None:
        # the paired delta + CI live on the bootstrap `stats`; `effect` carries
        # effect sizes. Read delta/CI from stats so a null delta never surfaces.
        st = cf.stats
        comparison = {
            "arm_a": cf.arm_a,
            "arm_b": cf.arm_b,
            "delta": st.get("mean_delta"),
            "ci_low": st.get("ci_low"),
            "ci_high": st.get("ci_high"),
            "ci_method": st.get("ci_method"),
            "ci_level": st.get("ci_level"),
            "mde": findings.mde.value,
            # NB: this is the multi-arm "primary pair" flag [PRA-M4] — whether
            # this pair carries the pre-registered decision — NOT whether the
            # official fence passed. Authority (official vs exploratory) is
            # `provenance.mode`; naming it official_decision would falsely read as
            # a fenced result on an exploratory card.
            "is_primary_pair": cf.official_decision,
            "detected": cf.decision.get("detected"),
            "decides_positive": cf.decision.get("decides_positive"),
            "excluded_from_official": cf.excluded_from_official,
        }

    selfcheck_events = find_events(ledger_path, events.SELFCHECK)
    selfcheck = (
        "passed" if selfcheck_events and selfcheck_events[-1].get("passed") else
        ("failed" if selfcheck_events else "absent")
    )
    excluded_metrics = [
        c.label for c in findings.comparisons if c.excluded_from_official
    ]
    forensic_quarantines = [
        q["trial_id"] for q in (findings.forensics or {}).get("quarantined", [])
    ]

    return ResultCard(
        schema_version=CARD_SCHEMA_VERSION,
        instrument={
            "version": prov.instrument_version,
            "git_sha": prov.instrument_git_sha,
            "tier": "ADVISORY",
        },
        battery=_battery(ledger_path, task_ids, corpus_manifest),
        primary_metric=primary,
        decision_rule=findings.decision_rule,
        arms=arms,
        comparison=comparison,
        provenance={
            "spec_sha256": lock.get("spec_sha256"),
            "lock_commitment_sha": (lock.get("task_commitment") or {}).get("task_shas_sha256"),
            "ledger_head": prov.ledger_head_hash,
            "chain_ok": prov.chain_ok,
            "mode": mode,
            # F-H5: the render this card certifies, checkable against the chain
            # without recomputing.
            "rendered_head_hash": rendered.get("rendered_head_hash"),
            "findings_sha256": rendered.get("findings_sha256"),
            "selfcheck": selfcheck,
            "rubric_committed": findings.rubric_committed,
        },
        disclosures={
            "confounds": [c.get("flag") for c in findings.confounds],
            "contamination": findings.contamination,
            "forensic_quarantines": forensic_quarantines,
            "excluded_metrics": excluded_metrics,
        },
    )


def serialize_card(card: Union[ResultCard, dict]) -> str:
    """Canonical, byte-deterministic JSON — the citable, diffable artifact.

    Accepts the typed card or its re-loaded dict form; the model dumps to
    exactly the dict it mirrors, so the bytes are unchanged (golden-pinned)
    [refactor 07 §5]."""
    payload = card.model_dump(mode="json") if isinstance(card, ResultCard) else card
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- human-facing renders (pure projections of the typed card) --------------
def _fmt_score(v) -> str:
    return "n/a (pairwise-only)" if v is None else f"{v:.4f}"


def _fmt(v) -> str:
    return "n/a" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def _pct(v) -> str:
    """CI level as a percent, matching the findings render (e.g. 0.95 → '95%')."""
    return "n/a" if v is None else f"{int(round(v * 100))}%"


def render_card_markdown(card: ResultCard) -> str:
    """A human-readable markdown render — deterministic, leads with the co-equal
    score + delta, and keeps every honesty stamp visible."""
    b = card.battery
    inst = card.instrument
    prov = card.provenance
    comp = card.comparison or {}
    lines: list[str] = []
    lines.append(f"# verdi-bench result card — {b.get('corpus_id') or 'experiment'}")
    lines.append("")
    lines.append(
        f"**Tier:** {inst['tier']} · **Mode:** {prov['mode']} · "
        f"**Primary metric:** `{card.primary_metric}` · **Battery n:** {b['n_tasks']}"
    )
    lines.append("")
    lines.append("## Scores (per arm)")
    lines.append("")
    lines.append("| arm | model | absolute score | n |")
    lines.append("|---|---|---|---|")
    for a in card.arms:
        lines.append(
            f"| {a['name']} | `{a['model']}` | {_fmt_score(a['absolute_score'])} | {a['n']} |"
        )
    lines.append("")
    lines.append("## Comparison (paired)")
    lines.append("")
    if comp:
        lines.append(
            f"`{comp['arm_a']}` vs `{comp['arm_b']}`: **delta = {_fmt(comp['delta'])}**, "
            f"{_pct(comp.get('ci_level'))} CI [{_fmt(comp['ci_low'])}, {_fmt(comp['ci_high'])}] "
            f"({comp.get('ci_method')}); detected: {comp.get('detected')}; "
            f"decides_positive: {comp.get('decides_positive')}."
        )
        lines.append("")
        lines.append(f"Decision rule: `{card.decision_rule}` · MDE: {_fmt(comp.get('mde'))}")
        if prov["mode"] != "official":
            lines.append("")
            lines.append(
                "> This decision is **exploratory** (watermarked). An official, "
                "fenced finding requires `bench analyze --official`."
            )
    else:
        lines.append("_no comparison available_")
    lines.append("")
    lines.append("## Battery (comparability key)")
    lines.append("")
    dataset = b.get("dataset")
    ds = f" · dataset {dataset['name']}@{dataset['version']}" if dataset else ""
    lines.append(f"`battery_sha` = `{b['battery_sha']}`")
    lines.append("")
    lines.append(
        f"basis: **{b['battery_basis']}** · corpus: {b.get('corpus_id')} {b.get('semver') or ''}{ds}"
    )
    lines.append("")
    lines.append(
        "> Two cards are comparable **iff** their `battery_sha`, basis, and primary "
        "metric match. `bench card compare` refuses otherwise."
    )
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- spec_sha256: `{prov.get('spec_sha256')}`")
    lines.append(f"- lock_commitment_sha: `{prov.get('lock_commitment_sha')}`")
    lines.append(
        f"- ledger_head: `{prov.get('ledger_head')}` (chain {'ok' if prov.get('chain_ok') else 'BROKEN'})"
    )
    lines.append(f"- selfcheck: {prov.get('selfcheck')} · rubric committed: {prov.get('rubric_committed')}")
    lines.append("")
    d = card.disclosures
    lines.append("## Disclosures")
    lines.append("")
    lines.append(f"- confounds: {', '.join(d['confounds']) or 'none'}")
    contam = d.get("contamination") or {}
    # F-M-O4: asymmetric entries are dicts ({task_id, flagged_arms,
    # unflagged_arms}) — rendered with the same phrasing report.py uses, never
    # joined as strings (that was a TypeError whenever any asymmetry existed).
    asym = contam.get("asymmetric") or []
    lines.append(
        f"- contamination: probe {contam.get('probe_status', 'n/a')}; "
        f"asymmetric: {'; '.join(asymmetry_line(a) for a in asym) or 'none'}"
    )
    lines.append(
        f"- forensic quarantines: {', '.join(d.get('forensic_quarantines', [])) or 'none'}"
    )
    lines.append(f"- excluded metrics: {', '.join(d['excluded_metrics']) or 'none'}")
    lines.append("")
    lines.append(
        f"_Instrument {inst['version']} ({inst['git_sha'][:12]}). ADVISORY tier: a "
        "comparable number, not an authoritative leaderboard entry._"
    )
    return "\n".join(lines) + "\n"


def render_card_html(card: ResultCard) -> str:
    """A compact, self-contained HTML card (inline style, no external references)
    — the shareable/publishable artifact. Byte-deterministic for a fixed card."""
    import html as _html

    b = card.battery
    prov = card.provenance
    comp = card.comparison or {}
    inst = card.instrument

    def esc(x) -> str:
        return _html.escape(str(x))

    rows = "".join(
        f"<tr><td>{esc(a['name'])}</td><td><code>{esc(a['model'])}</code></td>"
        f"<td class=n>{esc(_fmt_score(a['absolute_score']))}</td><td class=n>{a['n']}</td></tr>"
        for a in card.arms
    )
    exploratory_note = (
        "" if prov["mode"] == "official"
        else " &middot; <b>exploratory</b> (not a fenced finding)"
    )
    delta_line = (
        f"<b>delta {esc(_fmt(comp.get('delta')))}</b>, "
        f"{esc(_pct(comp.get('ci_level')))} CI [{esc(_fmt(comp.get('ci_low')))}, "
        f"{esc(_fmt(comp.get('ci_high')))}] ({esc(comp.get('ci_method'))}); "
        f"detected {esc(comp.get('detected'))}; decides_positive "
        f"{esc(comp.get('decides_positive'))}{exploratory_note}"
        if comp else "no comparison"
    )
    dataset = b.get("dataset")
    ds = f" &middot; dataset {esc(dataset['name'])}@{esc(dataset['version'])}" if dataset else ""
    # F-M-O5: content parity with the markdown card's Disclosures section — the
    # shareable artifact must not be the disclosure-free one.
    d = card.disclosures
    contam = d.get("contamination") or {}
    asym = contam.get("asymmetric") or []
    disclosures = "".join(
        f"<li>{esc(item)}</li>"
        for item in (
            f"confounds: {', '.join(d['confounds']) or 'none'}",
            f"contamination: probe {contam.get('probe_status', 'n/a')}; asymmetric: "
            + ("; ".join(asymmetry_line(a) for a in asym) or "none"),
            f"forensic quarantines: {', '.join(d.get('forensic_quarantines', [])) or 'none'}",
            f"excluded metrics: {', '.join(d['excluded_metrics']) or 'none'}",
        )
    )
    style = (
        "body{font:14px system-ui,sans-serif;margin:2rem;max-width:52rem}"
        "table{border-collapse:collapse;width:100%;margin:.5rem 0}"
        "td,th{border:1px solid #ccc;padding:.3rem .5rem;text-align:left}"
        ".n{text-align:right;font-variant-numeric:tabular-nums}"
        "code{background:#f2f2f2;padding:0 .2rem;border-radius:3px;word-break:break-all}"
        ".stamp{color:#666;font-size:.85em}.sha{font-family:monospace;word-break:break-all}"
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<title>verdi-bench result card</title><style>{style}</style></head><body>"
        f"<h1>verdi-bench result card &mdash; {esc(b.get('corpus_id') or 'experiment')}</h1>"
        f"<p class=stamp>Tier {esc(inst['tier'])} &middot; mode {esc(prov['mode'])} &middot; "
        f"metric <code>{esc(card.primary_metric)}</code> &middot; battery n {esc(b['n_tasks'])}</p>"
        f"<h2>Scores</h2><table><tr><th>arm</th><th>model</th><th>absolute score</th><th>n</th></tr>{rows}</table>"
        f"<h2>Comparison</h2><p>{delta_line}<br><span class=stamp>rule "
        f"<code>{esc(card.decision_rule)}</code> &middot; MDE {esc(_fmt(comp.get('mde')))}</span></p>"
        f"<h2>Battery</h2><p class=sha>{esc(b['battery_sha'])}</p>"
        f"<p class=stamp>basis {esc(b['battery_basis'])}{ds}. Comparable iff battery_sha + basis + metric match.</p>"
        f"<h2>Provenance</h2><p class=sha>spec {esc(prov.get('spec_sha256'))}<br>"
        f"ledger head {esc(prov.get('ledger_head'))} (chain {'ok' if prov.get('chain_ok') else 'BROKEN'}) "
        f"&middot; selfcheck {esc(prov.get('selfcheck'))}</p>"
        f"<h2>Disclosures</h2><ul>{disclosures}</ul>"
        f"<p class=stamp>Instrument {esc(inst['version'])}. ADVISORY: a comparable number, "
        "not an authoritative leaderboard entry.</p>"
        "</body></html>"
    )


# --- comparability ---------------------------------------------------------
# A card for comparison: the typed model, or its re-loaded JSON dict (`bench
# card compare` reads card files) — the model's mapping reads keep one code path.
CardLike = Union[ResultCard, dict]


def _comparability_key(card: CardLike) -> tuple:
    b = card.get("battery", {})
    return (b.get("battery_sha"), b.get("battery_basis"), card.get("primary_metric"))


def compare_cards(card_a: CardLike, card_b: CardLike) -> dict:
    """Compare two cards, refusing loudly across different task sets/metrics.

    Comparable iff ``(battery_sha, battery_basis, primary_metric)`` match — i.e.
    the two runs graded the *same tasks* on the *same metric*. Returns a
    side-by-side of the per-arm absolute scores and each run's paired delta.
    """
    ka, kb = _comparability_key(card_a), _comparability_key(card_b)
    if ka != kb:
        reasons = []
        if ka[0] != kb[0] or ka[1] != kb[1]:
            reasons.append(
                f"different task set (battery {ka[0]!r}/{ka[1]} vs {kb[0]!r}/{kb[1]})"
            )
        if ka[2] != kb[2]:
            reasons.append(f"different primary metric ({ka[2]!r} vs {kb[2]!r})")
        raise CardError("cards are not comparable: " + "; ".join(reasons))

    def _scores(card: CardLike) -> dict:
        # carry the model: two comparable cards may reuse an arm NAME for
        # different models, so a name-only side-by-side would silently compare
        # unlike models.
        return {a["name"]: {"model": a["model"], "absolute_score": a["absolute_score"], "n": a["n"]}
                for a in card.get("arms", [])}

    def _delta(card: CardLike):
        c = card.get("comparison") or {}
        return {"arm_a": c.get("arm_a"), "arm_b": c.get("arm_b"),
                "delta": c.get("delta"), "ci_low": c.get("ci_low"), "ci_high": c.get("ci_high")}

    return {
        "comparable": True,
        "battery_sha": ka[0],
        "battery_basis": ka[1],
        "primary_metric": ka[2],
        "arms": {"a": _scores(card_a), "b": _scores(card_b)},
        "comparison": {"a": _delta(card_a), "b": _delta(card_b)},
    }
