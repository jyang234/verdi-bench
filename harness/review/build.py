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
from .sample import comparisons_from_ledger as records_from_ledger
from .sample import select_for_review


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
            continue
        if _swap(seed, sel.comparison_id):
            first_arm, first, second_arm, second = (
                arm_b.name, cmp.response_b, arm_a.name, cmp.response_a
            )
        else:
            first_arm, first, second_arm, second = (
                arm_a.name, cmp.response_a, arm_b.name, cmp.response_b
            )
        response_map = {"1": first_arm, "2": second_arm}
        events.record_review_packet_built(
            ledger_path, ctx,
            comparison_id=sel.comparison_id, task_id=cmp.task_id,
            task_class=cmp.task_class, response_map=response_map, seed=seed,
        )
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
