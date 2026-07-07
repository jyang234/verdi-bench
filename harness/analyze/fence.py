"""Itemized official-fence report [EVAL-14 AC-6, AC-7].

``render_markdown`` enforces the official fence fail-fast — the first unmet
requirement raises. An observer needs the opposite projection: every requirement
named with its own state, so "why can't I render official?" answers itself on a
screen. This module itemizes the SAME ordered :data:`~harness.analyze.findings.fence.FENCE_CHECKS`
the render side raises on (imported, not re-implemented), so the D8 drift class —
a check that exists on one side only — is unrepresentable [refactor 07 §1].

Read-only and side-effect-free: no event is appended (``cant_analyze`` emission
lives in the analyze CLI, not here), nothing is rendered. States: ``ok`` |
``failed`` | ``unchecked`` — a check that *requires* the corpus manifest is
``unchecked`` (with the reason) when none is supplied, and the fence is then not
passable, exactly as the render would refuse [EVAL-8 AC-2]. The two render-scoped
validations (findings provenance and the selfcheck↔deployed CI-method agreement)
are re-run per render and are out of scope for a ledger-level checklist; the
selfcheck item names the validated method so the render-time agreement is
inspectable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from ..ledger import events
from ..ledger.query import find_events, verify
from ..schema.errors import SpecError
from ..schema.experiment import ExperimentSpec
from .findings.fence import (
    FENCE_CHECKS,
    FenceContext,
    effective_multi_arm_correction,
)

# Checks the observer cannot evaluate without the spec's corpus identity — omitted
# (not failed) when experiment.yaml is unreadable, exactly as they were before.
_SPEC_CORPUS_CHECKS = frozenset({"corpus_identity", "corpus_coverage", "calibration"})


def _item(item_id: str, name: str, state: str, detail: str) -> dict:
    return {"id": item_id, "name": name, "state": state, "detail": detail}


def official_fence_report(experiment_dir, *, corpus_manifest=None) -> dict:
    """Every official-fence requirement with its current state.

    Returns ``{official_ready, items}``; ``official_ready`` is true only when
    every item is ``ok`` — an ``unchecked`` item (no manifest supplied) blocks
    readiness exactly as the render's refusal would.
    """
    experiment_dir = Path(experiment_dir)
    ledger_path = experiment_dir / "ledger.ndjson"
    items: list[dict] = []

    # 1. chain integrity — nothing downstream is trustworthy without it.
    chain = verify(ledger_path)
    items.append(
        _item(
            "chain",
            "hash chain verifies",
            "ok" if chain.ok else "failed",
            "" if chain.ok else (chain.detail or "chain verification failed"),
        )
    )
    if not chain.ok:
        # Fail closed: the remaining checks would read unverified content.
        return {"official_ready": False, "items": items}

    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    lock = locks[0] if locks else None
    try:
        spec = ExperimentSpec.from_yaml(experiment_dir / "experiment.yaml")
    except (SpecError, yaml.YAMLError, OSError):
        # An unreadable/absent spec is a describable state for an observer —
        # reported as the named failed item below, never smoothed over.
        spec = None

    items.append(
        _item(
            "lock",
            "experiment locked",
            "ok" if lock else "failed",
            "" if lock else "no experiment_locked event; run `bench plan` first",
        )
    )

    if spec is None:
        # The spec-dependent corpus checks cannot run; name the corpus item failed,
        # skip coverage/calibration, and mark the correction item unchecked — the
        # ledger-only checks (rubric/selfcheck/contamination/insulation) still run.
        items.append(
            _item("corpus_identity", "pre-registered corpus cited", "failed",
                  "experiment.yaml missing or unreadable")
        )
        ctx = FenceContext(
            ledger_path=ledger_path, corpus_manifest=corpus_manifest,
            spec_corpus={}, lock=lock or {}, correction="none",
        )
        for check in FENCE_CHECKS:
            if check.id in _SPEC_CORPUS_CHECKS:
                continue
            if check.id == "correction":
                items.append(_item("correction", check.name, "unchecked",
                                   "experiment.yaml missing or unreadable"))
                continue
            outcome = check.evaluate(ctx)
            items.append(_item(check.id, check.name, outcome.state, outcome.detail))
        return {"official_ready": all(i["state"] == "ok" for i in items), "items": items}

    # Normal path: build the same context the render evaluates against (no computed
    # findings, so no deployed-CI-method or summary-flag fallback), and itemize the
    # SAME ordered fence-check list the render fails fast on [refactor 07 §1].
    ctx = FenceContext(
        ledger_path=ledger_path,
        corpus_manifest=corpus_manifest,
        spec_corpus={"id": spec.corpus.id, "version": spec.corpus.version},
        lock=lock or {},
        correction=effective_multi_arm_correction(spec),
    )
    for check in FENCE_CHECKS:
        outcome = check.evaluate(ctx)
        items.append(_item(check.id, check.name, outcome.state, outcome.detail))

    return {
        "official_ready": all(i["state"] == "ok" for i in items),
        "items": items,
    }
