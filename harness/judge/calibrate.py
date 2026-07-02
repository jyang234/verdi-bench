"""Judge calibration [EVAL-2 §M6, AC-7].

The judge's authority is *earned* per task class through measured Cohen's kappa
against the human — who alone closes comparisons. Below-threshold classes are
flagged for panel escalation (v1 = flag only; panel is v2). Until
``min_human_verdicts`` are present for a class, kappa is "insufficient".

Kappa is hand-rolled (fixture-verified) to avoid an sklearn dependency [plan
choice].
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from ..ledger import events
from ..ledger.query import find_events


def cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """Cohen's kappa between two equal-length label sequences."""
    if len(labels_a) != len(labels_b):
        raise ValueError("label sequences must be equal length")
    n = len(labels_a)
    if n == 0:
        raise ValueError("no paired labels")
    categories = sorted(set(labels_a) | set(labels_b))
    # observed agreement
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    # expected agreement
    count_a = {c: labels_a.count(c) / n for c in categories}
    count_b = {c: labels_b.count(c) / n for c in categories}
    pe = sum(count_a[c] * count_b[c] for c in categories)
    if pe == 1.0:
        return 1.0  # degenerate: all one category and identical
    return (po - pe) / (1 - pe)


@dataclass
class ClassCalibration:
    task_class: str
    n: int
    kappa: Optional[float]
    sufficient: bool
    escalate: bool  # below threshold with sufficient data


def kappa_by_class(
    pairs: list[dict],
    *,
    kappa_threshold: float = 0.6,
    min_human_verdicts: int = 20,
) -> dict[str, ClassCalibration]:
    """``pairs`` = ``[{task_class, judge_winner, human_winner}, ...]``.

    Per class: kappa once ``min_human_verdicts`` are present; classes below the
    threshold (with sufficient data) are escalation candidates.
    """
    by_class: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_class[p["task_class"]].append(p)

    out: dict[str, ClassCalibration] = {}
    for cls, items in by_class.items():
        n = len(items)
        if n < min_human_verdicts:
            out[cls] = ClassCalibration(cls, n, kappa=None, sufficient=False, escalate=False)
            continue
        k = cohens_kappa(
            [i["judge_winner"] for i in items], [i["human_winner"] for i in items]
        )
        out[cls] = ClassCalibration(
            cls, n, kappa=k, sufficient=True, escalate=k < kappa_threshold
        )
    return out


# --- ledger state machine: only human verdicts close comparisons [AC-7] ----
def comparison_closed(ledger_path, comparison_id: str) -> bool:
    """A comparison is closed iff a human_verdict exists for it — a judge
    verdict alone (advisory) never closes it [D004]."""
    for ev in find_events(ledger_path, events.HUMAN_VERDICT):
        if ev["verdict"].get("comparison_id") == comparison_id:
            return True
    return False


def pairs_from_ledger(ledger_path) -> list[dict]:
    """Build judge/human paired labels by comparison_id for kappa."""
    judge = {
        e["verdict"].get("comparison_id"): e["verdict"]
        for e in find_events(ledger_path, events.JUDGE_VERDICT)
    }
    pairs: list[dict] = []
    for e in find_events(ledger_path, events.HUMAN_VERDICT):
        hv = e["verdict"]
        cid = hv.get("comparison_id")
        jv = judge.get(cid)
        if jv is None:
            continue
        pairs.append(
            {
                "task_class": hv.get("task_class") or "default",
                "judge_winner": jv["winner"],
                "human_winner": hv["winner"],
            }
        )
    return pairs
