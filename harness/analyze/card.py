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
from typing import Optional

from ..corpus.commit import content_sha
from ..ledger import events
from ..ledger.query import find_events
from .report import compute_findings, per_arm_absolute_scores

CARD_SCHEMA_VERSION = 1


class CardError(RuntimeError):
    """A card cannot be built or two cards cannot be compared — stated with the
    reason. Fail loud [master plan §7.7]: a card that silently omits provenance
    or silently compares mismatched batteries would defeat its own purpose."""


def _lock_event(ledger_path) -> dict:
    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    if not locks:
        raise CardError("no experiment_locked event: nothing to card")
    return locks[0]


def _rendered_mode(ledger_path) -> str:
    """The mode of the most recent findings render. A card certifies what was
    actually rendered, so `bench analyze` must have run first."""
    rendered = find_events(ledger_path, events.FINDINGS_RENDERED)
    if not rendered:
        raise CardError(
            "no findings_rendered event: run `bench analyze` before emitting a card"
        )
    return rendered[-1]["mode"]


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
) -> dict:
    """Project a completed, analyzed run into a result card (pure).

    ``task_ids`` are the committed task ids (the CLI reads them from tasks.yaml).
    Requires a prior ``bench analyze`` (the card certifies a rendered result).
    """
    mode = _rendered_mode(ledger_path)
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
            "official_decision": cf.official_decision,
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

    return {
        "schema_version": CARD_SCHEMA_VERSION,
        "instrument": {
            "version": prov.instrument_version,
            "git_sha": prov.instrument_git_sha,
            "tier": "ADVISORY",
        },
        "battery": _battery(ledger_path, task_ids, corpus_manifest),
        "primary_metric": primary,
        "decision_rule": findings.decision_rule,
        "arms": arms,
        "comparison": comparison,
        "provenance": {
            "spec_sha256": lock.get("spec_sha256"),
            "lock_commitment_sha": (lock.get("task_commitment") or {}).get("task_shas_sha256"),
            "ledger_head": prov.ledger_head_hash,
            "chain_ok": prov.chain_ok,
            "mode": mode,
            "selfcheck": selfcheck,
            "rubric_committed": findings.rubric_committed,
        },
        "disclosures": {
            "confounds": [c.get("flag") for c in findings.confounds],
            "contamination": findings.contamination,
            "excluded_metrics": excluded_metrics,
        },
    }


def serialize_card(card: dict) -> str:
    """Canonical, byte-deterministic JSON — the citable, diffable artifact."""
    return json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- comparability ---------------------------------------------------------
def _comparability_key(card: dict) -> tuple:
    b = card.get("battery", {})
    return (b.get("battery_sha"), b.get("battery_basis"), card.get("primary_metric"))


def compare_cards(card_a: dict, card_b: dict) -> dict:
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

    def _scores(card: dict) -> dict:
        return {a["name"]: {"absolute_score": a["absolute_score"], "n": a["n"]}
                for a in card.get("arms", [])}

    def _delta(card: dict):
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
