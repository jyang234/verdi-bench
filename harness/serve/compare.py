"""Paired A/B comparison payload [EVAL-14 AC-6].

Reuses the judge's own assembly (``native_comparisons_from_ledger`` — the same
pairing, the same review-packet diff artifacts) and joins each pair's grade
outcomes and advisory verdict. Deterministic and advisory tiers stay separate
fields, never blended into one score; the summary watermark is decided by the
SAME official fence ``bench analyze`` enforces (``official_fence_report``),
so the compare screen can never announce a verdict the render would refuse.

The A-vs-B highlight is presentation over the two diff-from-empty texts:
``difflib.SequenceMatcher`` opcodes, line-tokenized (stable, stdlib,
deterministic), shipped as segments the page renders side-by-side. Baseline
for delta signs is arm A — ``spec.arms[0]``, fixed by lock order, never a
toggle [wireframe D004 discussion / parity research §3].
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from ..analyze.fence import official_fence_report
from ..judge.assemble import native_comparisons_from_ledger
from ..ledger import events
from ..ledger.query import find_events
from ..run.flight_recorder import resolve_flight_recorder
from ..schema.errors import SpecError
from ..schema.experiment import ExperimentSpec


def _segments(a_text: str, b_text: str) -> list[dict]:
    """Line-tokenized diff segments: ``[{op, a, b}]`` with op ∈
    equal|replace|delete|insert; ``a``/``b`` are that side's text slice."""
    a_lines = a_text.splitlines(keepends=True)
    b_lines = b_text.splitlines(keepends=True)
    out: list[dict] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(
        None, a_lines, b_lines, autojunk=False
    ).get_opcodes():
        out.append({"op": op, "a": "".join(a_lines[i1:i2]), "b": "".join(b_lines[j1:j2])})
    return out


def _binary_by_trial(ledger_path) -> dict:
    return {
        ev["trial_id"]: ev.get("binary_score")
        for ev in find_events(ledger_path, events.GRADE)
    }


def paired_comparisons(experiment_dir, *, corpus_manifest=None) -> dict:
    """The compare screen's whole payload for one experiment."""
    experiment_dir = Path(experiment_dir)
    ledger_path = experiment_dir / "ledger.ndjson"
    try:
        spec = ExperimentSpec.from_yaml(experiment_dir / "experiment.yaml")
    except (SpecError, OSError) as e:
        return {"error": f"cannot compare without a readable spec: {e}", "pairs": []}

    arm_a, arm_b = spec.arms[0].name, spec.arms[1].name
    arm_a_model, arm_b_model = spec.arms[0].model, spec.arms[1].model
    grades = _binary_by_trial(ledger_path)
    verdicts = {
        (ev.get("verdict") or {}).get("comparison_id"): ev["verdict"]
        for ev in find_events(ledger_path, events.JUDGE_VERDICT)
    }
    trial_ids: dict[tuple, str] = {}
    # trial_id → (artifacts_path, flight_recorder_sha) for operator-tier reasoning
    # rendering [EVAL-24 AC-5]. The sha is the top-level trial-event field.
    trial_meta: dict[str, tuple] = {}
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        trial_ids[(rec["task_id"], rec.get("repetition", 0), rec["arm"])] = rec["trial_id"]
        trial_meta[rec["trial_id"]] = (rec.get("artifacts_path"), ev.get("flight_recorder_sha"))

    def _reasoning(trial_id):
        """Per-arm reasoning for the operator compare view — unblinded, sha-verified,
        None when the arm captured none [EVAL-24 AC-5]. Reasoning renders on the
        operator tier ONLY (here and the trial process view via ``trial_detail``
        [flight-recorder charter]): it is never in the judge packet or the fence."""
        meta = trial_meta.get(trial_id)
        if meta is None:
            return None
        _status, record = resolve_flight_recorder(meta[0], meta[1])
        if record is None:
            return None
        return [
            {"content": e.content, "tokens": e.tokens, "cost": e.cost, "agent": e.agent}
            for e in record.entries
        ]

    pairs: list[dict] = []
    for cmp_ in native_comparisons_from_ledger(ledger_path, spec):
        tid_a = trial_ids.get((cmp_.task_id, cmp_.repetition, arm_a))
        tid_b = trial_ids.get((cmp_.task_id, cmp_.repetition, arm_b))
        pass_a: Optional[bool] = grades.get(tid_a)
        pass_b: Optional[bool] = grades.get(tid_b)
        verdict = verdicts.get(cmp_.comparison_id)
        holdout_differs = (
            pass_a is not None and pass_b is not None and pass_a != pass_b
        )
        judge_pick = (verdict or {}).get("winner") in ("A", "B")
        pairs.append(
            {
                "comparison_id": cmp_.comparison_id,
                "task_id": cmp_.task_id,
                "repetition": cmp_.repetition,
                "a": {
                    "trial_id": tid_a,
                    "holdout_pass": pass_a,
                    "holdout_results": cmp_.response_a.holdout_results,
                    "reasoning": _reasoning(tid_a),  # operator-tier, unblinded [AC-5]
                },
                "b": {
                    "trial_id": tid_b,
                    "holdout_pass": pass_b,
                    "holdout_results": cmp_.response_b.holdout_results,
                    "reasoning": _reasoning(tid_b),  # operator-tier, unblinded [AC-5]
                },
                "judge": verdict,  # advisory tier, shown as its own line, never blended
                "disagreement": holdout_differs or (judge_pick and not holdout_differs),
                "segments": _segments(cmp_.response_a.diff, cmp_.response_b.diff),
            }
        )

    holdout_a = sum(1 for p in pairs if p["a"]["holdout_pass"] and not p["b"]["holdout_pass"])
    holdout_b = sum(1 for p in pairs if p["b"]["holdout_pass"] and not p["a"]["holdout_pass"])
    jw = [(p["judge"] or {}).get("winner") for p in pairs]
    fence = official_fence_report(experiment_dir, corpus_manifest=corpus_manifest)
    return {
        "arm_a": arm_a,
        "arm_b": arm_b,
        "arm_a_model": arm_a_model,
        "arm_b_model": arm_b_model,
        "official_ready": fence["official_ready"],
        "summary": {
            "pairs": len(pairs),
            "holdout": {
                "a_only": holdout_a,
                "b_only": holdout_b,
                "both": sum(1 for p in pairs if p["a"]["holdout_pass"] and p["b"]["holdout_pass"]),
                "neither": sum(
                    1 for p in pairs
                    if p["a"]["holdout_pass"] is False and p["b"]["holdout_pass"] is False
                ),
            },
            "judge": {
                "a": jw.count("A"),
                "b": jw.count("B"),
                "tie": jw.count("TIE"),
                "cant": jw.count("CANT_JUDGE"),
                "unjudged": sum(1 for w in jw if w is None),
            },
            "disagreements": sum(1 for p in pairs if p["disagreement"]),
        },
        "pairs": pairs,
    }
