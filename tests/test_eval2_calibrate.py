"""EVAL-2 AC-7 — kappa by class, human closes comparisons, escalation table."""

from __future__ import annotations

from harness.judge.calibrate import (
    cohens_kappa,
    comparison_closed,
    kappa_by_class,
    pairs_from_ledger,
)
from harness.judge.schema import Verdict, VerdictProvenance, Winner
from harness.ledger import events
from harness.ledger.events import append_human_verdict, append_verdict
from tests.fixtures.builders import fixed_ctx


def _prov():
    return VerdictProvenance(
        judge_model="google/gemini-1.5-pro-002", rubric_sha256="a", packet_sha256="b",
        call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t",
    )


def test_ac7_kappa_computation():
    # perfect agreement ⇒ kappa 1.0
    assert cohens_kappa(["A", "B", "A"], ["A", "B", "A"]) == 1.0
    # systematic anti-correlation with balanced marginals ⇒ negative kappa
    assert cohens_kappa(["A", "B"], ["B", "A"]) < 0


def test_ac7_escalation_table():
    # 20 agreeing + 20 disagreeing across two classes
    good = [{"task_class": "easy", "judge_winner": "A", "human_winner": "A"} for _ in range(20)]
    bad = []
    for i in range(20):
        jw = "A" if i % 2 == 0 else "B"
        hw = "B" if i % 2 == 0 else "A"  # judge anti-correlated with human
        bad.append({"task_class": "hard", "judge_winner": jw, "human_winner": hw})
    table = kappa_by_class(good + bad, kappa_threshold=0.6, min_human_verdicts=20)
    assert table["easy"].kappa == 1.0 and table["easy"].escalate is False
    assert table["hard"].kappa < 0.6 and table["hard"].escalate is True


def test_ac7_insufficient_human_verdicts():
    items = [{"task_class": "rare", "judge_winner": "A", "human_winner": "A"} for _ in range(5)]
    table = kappa_by_class(items, min_human_verdicts=20)
    assert table["rare"].sufficient is False
    assert table["rare"].kappa is None


def test_ac7_human_verdict_closes(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    jv = Verdict(winner=Winner.A, reason="x",
                 evidence=[{"kind": "diff", "response": "A", "hunk": "h"}],
                 provenance=_prov(), comparison_id="cmp-1")
    append_verdict(ledger, ctx, verdict=jv.model_dump(mode="json"))
    # judge verdict alone does NOT close the comparison (advisory only)
    assert comparison_closed(ledger, "cmp-1") is False
    hv = Verdict(winner=Winner.A, reason="agree",
                 evidence=[{"kind": "diff", "response": "A", "hunk": "h"}],
                 provenance=_prov(), source="human", comparison_id="cmp-1")
    append_human_verdict(ledger, ctx, verdict=hv.model_dump(mode="json"))
    assert comparison_closed(ledger, "cmp-1") is True


def test_ac7_pairs_from_ledger(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    for i in range(3):
        jv = Verdict(winner=Winner.A, reason="x",
                     evidence=[{"kind": "diff", "response": "A", "hunk": "h"}],
                     provenance=_prov(), comparison_id=f"c{i}", task_class="cls")
        append_verdict(ledger, ctx, verdict=jv.model_dump(mode="json"))
        hv = Verdict(winner=Winner.B, reason="y",
                     evidence=[{"kind": "diff", "response": "B", "hunk": "h"}],
                     provenance=_prov(), source="human", comparison_id=f"c{i}", task_class="cls")
        append_human_verdict(ledger, ctx, verdict=hv.model_dump(mode="json"))
    pairs = pairs_from_ledger(ledger)
    assert len(pairs) == 3
    assert all(p["judge_winner"] == "A" and p["human_winner"] == "B" for p in pairs)
