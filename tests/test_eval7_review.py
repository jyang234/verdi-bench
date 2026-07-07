"""EVAL-7 — human review packet: blinding, sampling, capture-then-reveal, kappa."""

from __future__ import annotations

import pytest

from harness.judge.calibrate import comparison_closed, pairs_from_ledger
from harness.judge.schema import Evidence, Verdict, VerdictProvenance, Winner
from harness.ledger.events import append_human_verdict, append_verdict
from harness.review.kappa import (
    KappaEstimator,
    ReviewedItem,
    estimate_kappa,
    kappa_report,
    keyed_kappa_gate,
    weighted_kappa,
)
from harness.review.packet import ReviewPacketItem, ReviewResponse, build_review_packet
from harness.review.record import (
    ReviewError,
    RevealError,
    record_human_verdict,
    reveal_comparison,
)
from harness.review.sample import (
    ComparisonRecord,
    reviewed_kappa_items,
    select_for_review,
)
from harness.review.scrub import ScrubError, assert_identity_free, blind_scrub
from tests.fixtures.builders import ctx_for

_CANARIES = ["control", "treatment", "anthropic/claude-3-5-sonnet-20241022",
             "openai/gpt-4o-2024-08-06"]


def _prov(model="google/gemini-1.5-pro-002"):
    return VerdictProvenance(
        judge_model=model, rubric_sha256="a", packet_sha256="b",
        call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t",
    )


def _human(winner, cid, source="human", task_class="cls"):
    ev = [Evidence(kind="diff", response=winner, hunk="h")] if winner in ("A", "B") else []
    return Verdict(winner=Winner(winner), reason="r", evidence=ev, provenance=_prov("human"),
                   source=source, comparison_id=cid, task_class=task_class)


def _seed_judge(ledger, ctx, cid, winner="A"):
    """Append the judge verdict a comparison must have before a human reviews it."""
    ev = [Evidence(kind="diff", response=winner, hunk="h")] if winner in ("A", "B") else []
    jv = Verdict(winner=Winner(winner), reason="x", evidence=ev, provenance=_prov(),
                 comparison_id=cid, task_class="cls")
    append_verdict(ledger, ctx, verdict=jv.model_dump(mode="json"))


def _seed_packet_built(ledger, ctx, cid, response_map=None):
    """Seed the review_packet_built map a reveal reads to disclose real arm
    identities [D-P4-1]. Default map orients Response 1→arm_a, 2→arm_b."""
    from harness.ledger.events import record_review_packet_built

    record_review_packet_built(
        ledger, ctx, comparison_id=cid, task_id="t", task_class="cls",
        response_map=response_map or {"1": "arm_a", "2": "arm_b"}, seed=1,
    )


# --- AC-1: scrub shares the blinding core -----------------------------------
def test_ac1_scrub_canaries():
    item = ReviewPacketItem(
        comparison_id="cmp-1",
        task_prompt="Fix the bug. The control arm used claude-code.\nassistant: hi",
        response1=ReviewResponse(diff="control changed foo.py", holdout_results=[{"r": "pass"}]),
        response2=ReviewResponse(diff="treatment changed foo.py via openai/gpt-4o-2024-08-06",
                                 holdout_results=[{"r": "fail"}]),
    )
    html = build_review_packet([item], canaries=_CANARIES)
    for canary in ("control", "treatment", "claude-code", "openai", "gpt-4o", "assistant:"):
        assert canary not in html, canary
    # the shared core is what enforces it
    assert "[REDACTED]" in blind_scrub("the control arm", _CANARIES)
    with pytest.raises(ScrubError):
        assert_identity_free("leftover claude-code", None)


# --- AC-2: sampling ---------------------------------------------------------
def _records():
    return [
        ComparisonRecord("c1", "cls", "A", False, "B"),          # det-vs-judge conflict
        ComparisonRecord("c2", "cls", "TIE", True, "A"),         # order_inconsistent
        ComparisonRecord("c3", "cls", "CANT_JUDGE", False, "A"), # cant judge
        ComparisonRecord("c4", "cls", "A", False, "A"),          # agreement
        ComparisonRecord("c5", "cls", "B", False, "B"),          # agreement
        ComparisonRecord("c6", "cls", "A", False, "A"),          # agreement
        ComparisonRecord("c7", "cls", "B", False, "B"),          # agreement
        ComparisonRecord("c8", "cls", "A", False, "A"),          # agreement
    ]


def test_ac2_mandatory_set():
    selected = select_for_review(_records(), seed=1234)
    mandatory = {s.comparison_id for s in selected if s.stratum == "mandatory"}
    assert mandatory == {"c1", "c2", "c3"}  # exactly the disagreements


def test_ac2_random_floor_seeded():
    a = select_for_review(_records(), seed=1234)
    b = select_for_review(_records(), seed=1234)
    floor_a = [s.comparison_id for s in a if s.stratum == "floor"]
    floor_b = [s.comparison_id for s in b if s.stratum == "floor"]
    assert floor_a == floor_b  # reproducible for a seed
    # 5 agreements ⇒ ceil(0.2*5) = 1 floor item
    assert len(floor_a) == 1
    # a different seed can select a different floor member
    others = {
        tuple(s.comparison_id for s in select_for_review(_records(), seed=k) if s.stratum == "floor")
        for k in range(20)
    }
    assert len(others) > 1


def test_ac2_kappa_reviewed_only(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    # two comparisons judged; only one reviewed (has a human verdict + selected)
    for cid, jw, hw in [("c1", "A", "A"), ("c2", "A", "B")]:
        jv = Verdict(winner=Winner(jw), reason="x",
                     evidence=[Evidence(kind="diff", response=jw, hunk="h")],
                     provenance=_prov(), comparison_id=cid, task_class="cls")
        append_verdict(ledger, ctx, verdict=jv.model_dump(mode="json"))
    append_human_verdict(ledger, ctx, verdict=_human("A", "c1").model_dump(mode="json"),
                         arm_recognized=False)
    selected = select_for_review(
        [ComparisonRecord("c1", "cls", "A", False, "A"),
         ComparisonRecord("c2", "cls", "A", False, "B")],
        seed=1,
    )
    items = reviewed_kappa_items(ledger, selected)
    # c2 has no human verdict ⇒ excluded from kappa inputs; only c1 remains
    assert len(items) == 1
    assert items[0].a == "A" and items[0].b == "A"


def test_rv7_review_order_is_seeded_shuffle_not_disagreements_first():
    """RV-7: the reviewed set's order is a seeded shuffle of the id-sorted set —
    the mandatory (disagreement) items are NOT a prefix, so the disagreement
    boundary is not recoverable from packet order. Deleting the shuffle fails
    both assertions."""
    from harness.plan.seeds import seeded_shuffle, sub_seed

    seed = 4242
    records = (
        [ComparisonRecord(f"d{i}", "cls", "A", False, "B") for i in range(6)]  # disagreements
        + [ComparisonRecord(f"a{i}", "cls", "A", False, "A") for i in range(20)]  # agreements
    )
    result = select_for_review(records, seed)

    # (1) the order equals seeded_shuffle(sorted-by-id, sub_seed(seed,"review_order"))
    resorted = sorted(result, key=lambda s: s.comparison_id)
    expected = seeded_shuffle(resorted, sub_seed(seed, "review_order"))
    assert [s.comparison_id for s in result] == [s.comparison_id for s in expected]

    # (2) the mandatory (disagreement) items are not a prefix of the order
    mandatory_ids = {r.comparison_id for r in records if r.comparison_id.startswith("d")}
    prefix = {s.comparison_id for s in result[: len(mandatory_ids)]}
    assert prefix != mandatory_ids


def test_rv9_reveal_and_kappa_agree_on_duplicate_verdict(tmp_path):
    """RV-9: on a duplicated ledger, the reveal join is now last-wins, matching
    the (already last-wins) kappa join — both resolve to the LAST judge verdict."""
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    cid = "c1"

    def _jv(winner, call_ids):
        prov = VerdictProvenance(
            judge_model="google/gemini-1.5-pro-002", rubric_sha256="a", packet_sha256="b",
            call_ids=call_ids, orders="both", temperature=0.0, ts="t",
        )
        return Verdict(winner=Winner(winner), reason="x",
                       evidence=[Evidence(kind="diff", response=winner, hunk="h")],
                       provenance=prov, comparison_id=cid, task_class="cls")

    append_verdict(ledger, ctx, verdict=_jv("A", ["j1a", "j1b"]).model_dump(mode="json"))
    append_verdict(ledger, ctx, verdict=_jv("B", ["j2a", "j2b"]).model_dump(mode="json"))
    _seed_packet_built(ledger, ctx, cid)
    append_human_verdict(ledger, ctx, verdict=_human("A", cid).model_dump(mode="json"),
                         arm_recognized=False)

    rec = reveal_comparison(ledger, ctx, comparison_id=cid)
    assert rec["revealed"]["judge_verdict_id"] == "j2a"  # last judge verdict wins

    selected = select_for_review([ComparisonRecord(cid, "cls", "B", False, "A")], seed=1)
    items = reviewed_kappa_items(ledger, selected)
    assert len(items) == 1
    assert items[0].a == "B"  # kappa also scores the LAST judge winner


def test_rv8f_integrity_less_human_excluded_from_kappa(tmp_path):
    """RV-8(f): a human verdict with no integrity block is excluded from kappa
    items — the same gate the reveal and the integrity-rate already apply."""
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "c1", winner="A")
    # a human verdict recorded WITHOUT the integrity block (bare constructor path)
    append_human_verdict(ledger, ctx, verdict=_human("A", "c1").model_dump(mode="json"))
    selected = select_for_review([ComparisonRecord("c1", "cls", "A", False, "A")], seed=1)
    assert reviewed_kappa_items(ledger, selected) == []


# --- AC-3: packet is self-contained + leaks nothing -------------------------
def _packet_html():
    item = ReviewPacketItem(
        comparison_id="cmp-1", task_prompt="Fix the parser.",
        response1=ReviewResponse(diff="--- a\n+++ b\n+ ok", holdout_results=[{"id": "h1"}]),
        response2=ReviewResponse(diff="--- a\n+++ b\n+ nope", holdout_results=[{"id": "h1"}]),
    )
    return build_review_packet([item], canaries=_CANARIES)


def test_ac3_html_selfcontained():
    html = _packet_html()
    assert html.startswith("<!doctype html>")
    # no external requests: no absolute or protocol-relative URLs anywhere
    assert "://" not in html
    assert "src=" not in html and "//" not in html.replace("<!doctype", "")


def test_ac3_no_judge_or_arm_content():
    html = _packet_html()
    # no arm identities and no judge-verdict fields leak into the packet
    for forbidden in ("control", "treatment", "judge_verdict", "\"winner\"", "order_inconsistent"):
        assert forbidden not in html
    # responses are presented blinded as Response 1 / Response 2
    assert "Response 1" in html and "Response 2" in html


# --- AC-4: capture-then-reveal ----------------------------------------------
def test_ac4_verdict_event_schema(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "cmp-1")
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"),
                         arm_recognized=True, arm_guess="A", actual_arm="A")
    from harness.ledger.query import find_events
    ev = find_events(ledger, "human_verdict")[0]
    # mirrors judge verdict family + carries integrity
    assert ev["verdict"]["winner"] == "A"
    assert ev["verdict"]["source"] == "human"
    assert ev["integrity"] == {"arm_recognized": True, "arm_guess": "A", "actual_arm": "A"}


def test_ac4_integrity_pre_unblind(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    # reveal BEFORE any verdict is refused — the ordering is tool-enforced
    with pytest.raises(RevealError):
        reveal_comparison(ledger, ctx, comparison_id="cmp-1")


def test_ac4_reveal_after_verdict(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "cmp-1")
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"),
                         arm_recognized=False, arm_guess=None)
    _seed_packet_built(ledger, ctx, "cmp-1")
    rec = reveal_comparison(ledger, ctx, comparison_id="cmp-1")
    assert rec["event"] == "reveal"
    assert rec["verdict_event_id"] == "cmp-1"
    # reveal discloses the RECORDED map, not a hardcoded convention [RV-2]
    assert rec["revealed"]["arm_identities"] == {"1": "arm_a", "2": "arm_b"}


# --- AC-5: kappa feed + IPW estimator ---------------------------------------
def test_ac5_kappa_feed(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    jv = Verdict(winner=Winner.A, reason="x",
                 evidence=[Evidence(kind="diff", response="A", hunk="h")],
                 provenance=_prov(), comparison_id="c1", task_class="cls")
    append_verdict(ledger, ctx, verdict=jv.model_dump(mode="json"))
    # before a human verdict, the comparison is open and kappa has no pair
    assert comparison_closed(ledger, "c1") is False
    assert pairs_from_ledger(ledger) == []
    append_human_verdict(ledger, ctx, verdict=_human("A", "c1").model_dump(mode="json"),
                         arm_recognized=False)
    assert comparison_closed(ledger, "c1") is True
    pairs = pairs_from_ledger(ledger)
    assert len(pairs) == 1
    # F-L2: gated through the live shared gate (raw_pooled kappa_by_class is gone)
    items = {"cls": [ReviewedItem(pr["judge_winner"], pr["human_winner"], "mandatory")
                     for pr in pairs]}
    table = keyed_kappa_gate(items, weight="unweighted", categories=["A", "B", "TIE"],
                             kappa_threshold=0.6, estimator="raw_pooled", min_pairs=1)
    assert table["cls"].n == 1


def test_ac5_ipw_hand_checked():
    # 1 mandatory disagreement + 4 floor agreements; floor reweighted 1/0.2 = 5
    items = [
        ReviewedItem("A", "B", "mandatory"),
        ReviewedItem("A", "A", "floor"),
        ReviewedItem("A", "A", "floor"),
        ReviewedItem("B", "B", "floor"),
        ReviewedItem("B", "B", "floor"),
    ]
    ipw = estimate_kappa(items, KappaEstimator.ipw)
    # hand-derived: 1 - (21/221) (see kappa.py fixture derivation)
    assert abs(ipw - (1 - 21 / 221)) < 1e-9
    # raw pooled ignores the sampling bias and lands lower (8/13)
    raw = estimate_kappa(items, KappaEstimator.raw_pooled)
    assert abs(raw - 8 / 13) < 1e-9
    assert ipw > raw

    rep = kappa_report(items)
    assert rep.headline_method == "ipw"
    assert abs(rep.headline - ipw) < 1e-12
    assert rep.sensitivity is not None  # floor-only sensitivity present


def test_ac5_weighted_kappa_reduces_to_cohens():
    from harness.judge.calibrate import cohens_kappa
    a = ["A", "B", "A", "B", "TIE"]
    b = ["A", "A", "A", "B", "TIE"]
    assert abs(weighted_kappa(a, b, weight="unweighted") - cohens_kappa(a, b)) < 1e-12


# --- AC-6: integrity rate rides every finding -------------------------------
def test_ac6_integrity_rate_reported(tmp_path):
    from harness.analyze.report import compute_findings, render_markdown
    from tests.fixtures.builders import locked_experiment, seed_trial_and_grade

    ctx = ctx_for(tmp_path / "e")
    spec, _, ledger = locked_experiment(tmp_path / "e", ctx=ctx)
    for i in range(4):
        seed_trial_and_grade(ledger, ctx, trial_id=f"c-{i}", task_id=f"t{i}", arm="control",
                             passed=True, provenance={"image_digest": "d"})
        seed_trial_and_grade(ledger, ctx, trial_id=f"t-{i}", task_id=f"t{i}", arm="treatment",
                             passed=True, provenance={"image_digest": "d"})
    # two human reviews (of comparisons the judge produced), one recognized the arm
    _seed_judge(ledger, ctx, "t0")
    _seed_judge(ledger, ctx, "t1")
    record_human_verdict(ledger, ctx, verdict=_human("A", "t0"), arm_recognized=True,
                         arm_guess="A", actual_arm="A")
    record_human_verdict(ledger, ctx, verdict=_human("A", "t1"), arm_recognized=False,
                         arm_guess=None)
    findings = compute_findings(ledger, spec, spec.seed, coverage_n_sim=30, n_boot=300)
    assert findings.integrity["n_reviews"] == 2
    assert abs(findings.integrity["rate"] - 0.5) < 1e-9
    md = render_markdown(findings, ledger, "exploratory")
    assert "blinding integrity rate" in md.lower()
    # the field is schema-required — a findings doc cannot omit it
    assert "integrity" in findings.model_dump()


def test_rv1_refuses_duplicate_verdict(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "cmp-1")
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"), arm_recognized=False,
                         arm_guess=None)
    # a second verdict for the same comparison poisons kappa/integrity — refuse it
    with pytest.raises(ReviewError):
        record_human_verdict(ledger, ctx, verdict=_human("B", "cmp-1"), arm_recognized=False,
                             arm_guess=None)
    from harness.ledger.query import find_events
    assert len(find_events(ledger, "human_verdict")) == 1


def test_rv1_refuses_post_reveal_verdict(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "cmp-1")
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"), arm_recognized=False,
                         arm_guess=None)
    _seed_packet_built(ledger, ctx, "cmp-1")
    reveal_comparison(ledger, ctx, comparison_id="cmp-1")
    # a verdict after the unblinding is unblinded and must be refused (RV-1)
    with pytest.raises(ReviewError):
        record_human_verdict(ledger, ctx, verdict=_human("B", "cmp-1"), arm_recognized=False,
                             arm_guess=None)


def test_rv8_refuses_duplicate_reveal(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "cmp-1")
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"), arm_recognized=False,
                         arm_guess=None)
    _seed_packet_built(ledger, ctx, "cmp-1")
    reveal_comparison(ledger, ctx, comparison_id="cmp-1")
    with pytest.raises(RevealError):
        reveal_comparison(ledger, ctx, comparison_id="cmp-1")
    from harness.ledger.query import find_events
    assert len(find_events(ledger, "reveal")) == 1


def test_rv9_refuses_unjudged_comparison(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "cmp-1")
    # a verdict for a comparison the judge produced is fine
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"), arm_recognized=False,
                         arm_guess=None)
    # a mistyped comparison id (no judge verdict) would silently drop from kappa —
    # refuse it loudly instead (RV-9)
    with pytest.raises(ReviewError):
        record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-typo"),
                             arm_recognized=False, arm_guess=None)


def test_reveal_refuses_tampered_chain(tmp_path):
    """PL-6/AC-4: the reveal gate reads the ledger to check a human verdict
    exists; a forged human_verdict must not enable a premature unblinding."""
    import json

    from harness.ledger.chain import canonical_line
    from harness.ledger.query import ChainIntegrityError

    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    _seed_judge(ledger, ctx, "cmp-1")
    _seed_judge(ledger, ctx, "cmp-2")
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"),
                         arm_recognized=False, arm_guess=None)
    # a successor event so tampering the human-verdict line is chain-detectable
    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-2"),
                         arm_recognized=False, arm_guess=None)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    hv_idx = 2  # after the two judge seeds, the first human verdict is line index 2
    obj = json.loads(lines[hv_idx])
    obj["integrity"]["arm_recognized"] = True  # byte change breaks the chain at its successor
    lines[hv_idx] = canonical_line(obj)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ChainIntegrityError):
        reveal_comparison(ledger, ctx, comparison_id="cmp-1")


def test_m_o2_reveal_refused_until_the_whole_batch_is_verdicted(tmp_path):
    """F-M-O2: reveal was gated per ITEM — a reviewer could reveal item 1 (arm
    identities + judge verdict) and then record 'blinded' verdicts for the rest
    of the queue. With the queue ledgered as a review_batch, any reveal is
    refused until EVERY batched comparison carries its human verdict; legacy
    unbatched comparisons keep per-item semantics."""
    from harness.ledger.events import record_review_batch

    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    for cid in ("cmp-1", "cmp-2"):
        _seed_judge(ledger, ctx, cid)
        _seed_packet_built(ledger, ctx, cid)
    record_review_batch(ledger, ctx, batch_id="b1",
                        comparison_ids=["cmp-1", "cmp-2"], seed=7)

    record_human_verdict(ledger, ctx, verdict=_human("A", "cmp-1"),
                         arm_recognized=False, arm_guess=None)
    with pytest.raises(RevealError, match="cmp-2"):
        reveal_comparison(ledger, ctx, comparison_id="cmp-1")

    record_human_verdict(ledger, ctx, verdict=_human("B", "cmp-2"),
                         arm_recognized=False, arm_guess=None)
    rec = reveal_comparison(ledger, ctx, comparison_id="cmp-1")
    assert rec["event"] == "reveal"


def test_m_o2_build_ledgers_the_batch_idempotently(tmp_path):
    """The build verb ledgers the reviewed queue once; an identical re-build
    appends nothing (7A-4 stays true)."""
    import json

    import yaml

    from harness.ledger.query import find_events, read_events
    from harness.review.build import build_review
    from harness.schema.experiment import ExperimentSpec
    from tests.fixtures.builders import write_experiment_yaml

    expdir = tmp_path / "e"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml")
    spec = ExperimentSpec.from_yaml(expdir / "experiment.yaml")
    ledger = expdir / "l.ndjson"
    ctx = ctx_for(expdir)
    _seed_judge(ledger, ctx, "cmp-t0-r0")
    from tests.fixtures.builders import seed_trial_and_grade

    for arm, passed in (("control", True), ("treatment", False)):
        seed_trial_and_grade(ledger, ctx, trial_id=f"tr-{arm}", task_id="t0",
                             arm=arm, passed=passed)
    tasks = [{"id": "t0", "prompt": "p"}]
    build_review(ledger, spec, tasks, ctx, seed=7)
    batches = find_events(ledger, "review_batch")
    assert len(batches) == 1
    assert batches[0]["comparison_ids"] == ["cmp-t0-r0"]

    before = len(read_events(ledger))
    build_review(ledger, spec, tasks, ctx, seed=7)  # identical re-build
    assert len(read_events(ledger)) == before  # zero new events
