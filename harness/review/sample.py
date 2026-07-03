"""Review sampling [EVAL-7 §M1, D002, AC-2].

The reviewed set is the **mandatory** disagreement set plus a seed-derived 20%
random **floor** of agreements — an unbiased-direction kappa with a bounded
workload. Kappa is computed **only** over the human-reviewed set, and each
reviewed item records its stratum (``mandatory`` | ``floor``) so the D003
estimator (:mod:`.kappa`) can reweight the floor.

Mandatory set = every disagreement:

* deterministic-vs-judge conflicts (the holdout winner differs from the judge's),
* ``order_inconsistent`` judge verdicts (position bias downgraded them to TIE),
* ``CANT_JUDGE`` verdicts.

The floor is a reproducible function of the locked seed (namespaced sub-seed), so
the same plan yields the same floor. Ordering leaks nothing beyond order itself
(items are *not* labeled "disagreement") — that is the packet's concern.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..ledger import events
from ..ledger.query import find_events
from ..plan.seeds import index_at, sub_seed
from .kappa import ReviewedItem

FLOOR_FRACTION = 0.2


@dataclass(frozen=True)
class ComparisonRecord:
    comparison_id: str
    task_class: str
    judge_winner: str
    order_inconsistent: bool
    deterministic_winner: Optional[str]  # holdout-derived arm winner (A/B/TIE)


@dataclass(frozen=True)
class SelectedItem:
    comparison_id: str
    task_class: str
    stratum: str  # "mandatory" | "floor"


def _is_disagreement(rec: ComparisonRecord) -> bool:
    if rec.judge_winner == "CANT_JUDGE":
        return True
    if rec.order_inconsistent:
        return True
    if rec.deterministic_winner is not None and rec.judge_winner != rec.deterministic_winner:
        return True
    return False


def _deterministic_winner(pass_rate_a: float, pass_rate_b: float) -> str:
    if pass_rate_a > pass_rate_b:
        return "A"
    if pass_rate_b > pass_rate_a:
        return "B"
    return "TIE"


def comparisons_from_ledger(ledger_path, *, arm_a: str, arm_b: str) -> list[ComparisonRecord]:
    """Join judge verdicts with holdout-derived deterministic winners.

    The deterministic winner is the arm with the higher holdout pass rate for the
    verdict's **task** — resolved from the verdict's ``task_id`` (not by assuming
    ``comparison_id == task_id``, which broke once a comparison_id names a
    ``(task, repetition)`` pair). ``arm_a``/``arm_b`` fix the A/B orientation.
    """
    # per-(task, arm) holdout pass rate
    trials = {
        ev["trial_record"]["trial_id"]: ev["trial_record"]
        for ev in find_events(ledger_path, events.TRIAL)
    }
    passes: dict[str, dict[str, list[float]]] = {}
    for ev in find_events(ledger_path, events.GRADE):
        rec = trials.get(ev["trial_id"])
        if rec is None:
            continue
        passes.setdefault(rec["task_id"], {}).setdefault(rec["arm"], []).append(
            1.0 if ev["binary_score"] else 0.0
        )

    def rate(task_id: Optional[str], arm: str) -> Optional[float]:
        xs = passes.get(task_id, {}).get(arm) if task_id is not None else None
        return sum(xs) / len(xs) if xs else None

    records: list[ComparisonRecord] = []
    for ev in find_events(ledger_path, events.JUDGE_VERDICT):
        v = ev["verdict"]
        cid = v.get("comparison_id")
        if cid is None:
            continue
        task_id = v.get("task_id")
        ra, rb = rate(task_id, arm_a), rate(task_id, arm_b)
        det = _deterministic_winner(ra, rb) if ra is not None and rb is not None else None
        records.append(
            ComparisonRecord(
                comparison_id=cid,
                task_class=v.get("task_class") or "default",
                judge_winner=v["winner"],
                order_inconsistent=bool(v.get("order_inconsistent")),
                deterministic_winner=det,
            )
        )
    return records


def select_for_review(records: list[ComparisonRecord], seed: int) -> list[SelectedItem]:
    """Mandatory disagreements + a reproducible 20% floor of agreements [D002]."""
    mandatory = [r for r in records if _is_disagreement(r)]
    agreements = [r for r in records if not _is_disagreement(r)]

    selected = [
        SelectedItem(r.comparison_id, r.task_class, "mandatory") for r in mandatory
    ]

    # Reproducible floor: seeded shuffle of the sorted agreement ids, take a 20%
    # ceil so the floor is a true lower bound on agreement coverage.
    ordered = sorted(agreements, key=lambda r: r.comparison_id)
    k = math.ceil(FLOOR_FRACTION * len(ordered)) if ordered else 0
    if k > 0:
        base = sub_seed(seed, "review_floor")
        shuffled = list(ordered)
        for i in range(len(shuffled) - 1, 0, -1):
            j = index_at(base, i, i + 1)
            shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
        for r in shuffled[:k]:
            selected.append(SelectedItem(r.comparison_id, r.task_class, "floor"))

    # RV-7: order the whole reviewed set by a seeded shuffle so the mandatory/floor
    # (disagreement) boundary is NOT recoverable from packet order — two
    # independently id-sorted blocks would mark exactly which items are
    # disagreements. The stratum stays recorded per item (for IPW reweighting),
    # it is simply not reconstructable from the order the reviewer sees.
    selected.sort(key=lambda s: s.comparison_id)
    order_base = sub_seed(seed, "review_order")
    for i in range(len(selected) - 1, 0, -1):
        j = index_at(order_base, i, i + 1)
        selected[i], selected[j] = selected[j], selected[i]
    return selected


def reviewed_kappa_items(ledger_path, selected: list[SelectedItem]) -> list[ReviewedItem]:
    """Kappa inputs over the **reviewed set only** [AC-2].

    Joins judge and human winners by comparison_id, keeping only comparisons that
    (a) were selected for review and (b) have a human verdict. Unreviewed or
    still-open comparisons are excluded from kappa inputs.
    """
    strata = {s.comparison_id: s.stratum for s in selected}
    judge = {
        ev["verdict"].get("comparison_id"): ev["verdict"]["winner"]
        for ev in find_events(ledger_path, events.JUDGE_VERDICT)
    }
    items: list[ReviewedItem] = []
    for ev in find_events(ledger_path, events.HUMAN_VERDICT):
        v = ev["verdict"]
        cid = v.get("comparison_id")
        if cid not in strata or cid not in judge:
            continue
        items.append(ReviewedItem(a=judge[cid], b=v["winner"], stratum=strata[cid]))
    return items
