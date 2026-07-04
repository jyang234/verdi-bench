"""``bench review build`` [EVAL-7 §M1/M3, RV-2/3/6/7, D-P4-1].

Samples the reviewed set (mandatory disagreements + a seeded floor), renders the
offline blind packet, and records the Response-1/2 ↔ arm map for each comparison
as a ``review_packet_built`` event — the authoritative, hash-chained mapping that
reveal, ``review record`` (actual_arm / guess accuracy), and EVAL-9 process
scoring key off. Response order is randomized **per comparison**, so no arm sits
consistently in one column; the recorded ``response_map`` is the only truth.
"""

from __future__ import annotations

from ..blind.core import arm_canaries
from ..judge.assemble import comparisons_from_ledger as assemble_comparisons
from ..ledger import events
from ..ledger.events import EventContext
from ..plan.seeds import sub_seed
from .packet import ReviewPacketItem, ReviewResponse, build_review_packet
from .record import review_packet_built_for
from .sample import comparisons_from_ledger as records_from_ledger
from .sample import select_for_review


class ReviewBuildError(RuntimeError):
    """A review packet could not be built (e.g. a mandatory disagreement whose
    trial artifacts are missing) — fail loud rather than silently drop it [AC-2]."""


def _swap(seed: int, comparison_id: str) -> bool:
    """Per-comparison Response-1/2 order, deterministic in (seed, comparison_id)."""
    return sub_seed(seed, f"review_response_order:{comparison_id}") % 2 == 1


def build_review(ledger_path, spec, task_dicts, ctx: EventContext, *, seed: int):
    """Render the packet and record one ``review_packet_built`` per selected
    comparison. Returns ``(html, n_items)``."""
    arm_a, arm_b = spec.arms[0], spec.arms[1]
    task_classes = {t["id"]: t.get("task_class", "default") for t in task_dicts}
    prompts = {t["id"]: t.get("prompt", "") for t in task_dicts}

    artifacts = {
        c.comparison_id: c
        for c in assemble_comparisons(ledger_path, spec, task_classes=task_classes)
    }
    records = records_from_ledger(ledger_path, arm_a=arm_a.name, arm_b=arm_b.name)
    selected = select_for_review(records, seed)

    canaries = arm_canaries(spec.arms)
    items: list[ReviewPacketItem] = []
    for sel in selected:
        cmp = artifacts.get(sel.comparison_id)
        if cmp is None:
            # A mandatory disagreement with no assembled artifacts (its trials are
            # missing/unpaired) would silently vanish from review and kappa,
            # defeating "every disagreement is reviewed" — fail loud instead [AC-2].
            if sel.stratum == "mandatory":
                raise ReviewBuildError(
                    f"comparison {sel.comparison_id!r} is a mandatory disagreement "
                    "but has no assembled trial artifacts to review; refusing to "
                    "silently drop it from the reviewed set [EVAL-7 AC-2]"
                )
            continue
        # 7A-4: idempotent — one review_packet_built per comparison. If the
        # comparison already has a recorded packet event, reuse its ledgered
        # response_map (the authoritative blinding state) and append nothing, so
        # a re-run renders a byte-identical packet with zero new events. Only a
        # never-built comparison computes a fresh order and records it.
        existing = review_packet_built_for(ledger_path, sel.comparison_id)
        if existing is not None:
            response_map = existing["response_map"]
        else:
            if _swap(seed, sel.comparison_id):
                response_map = {"1": arm_b.name, "2": arm_a.name}
            else:
                response_map = {"1": arm_a.name, "2": arm_b.name}
            events.record_review_packet_built(
                ledger_path, ctx,
                comparison_id=sel.comparison_id, task_id=cmp.task_id,
                task_class=cmp.task_class, response_map=response_map, seed=seed,
            )
        # Map the recorded Response-1/2 arm names back to their trial responses.
        by_arm = {arm_a.name: cmp.response_a, arm_b.name: cmp.response_b}
        first, second = by_arm[response_map["1"]], by_arm[response_map["2"]]
        items.append(
            ReviewPacketItem(
                comparison_id=sel.comparison_id,
                task_prompt=prompts.get(cmp.task_id, ""),
                response1=ReviewResponse(diff=first.diff, holdout_results=first.holdout_results),
                response2=ReviewResponse(diff=second.diff, holdout_results=second.holdout_results),
            )
        )
    return build_review_packet(items, canaries=canaries), len(items)


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
def _review_build_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    d = Path(ctx_dir)
    events.record_review_packet_built(
        d / "ledger.ndjson", EventContext(experiment_id="prop"),
        comparison_id="cmp-prop", task_id="t", task_class="cls",
        response_map={"1": "arm_a", "2": "arm_b"}, seed=1,
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("review-build", _review_build_entrypoint)


_register()
