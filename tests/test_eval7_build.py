"""EVAL-7 RV-3/RV-7/D-P4-1 — the ``bench review build`` verb.

Before Phase 4 there was no ``review build`` verb; ``build_review_packet`` /
``select_for_review`` had zero production callers and nothing recorded which arm
was "Response 1/2". These tests drive ``bench review build`` and assert it emits
a ``review_packet_built`` event carrying the Response↔arm map, and writes the
offline packet.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, seed_trial_and_grade, write_experiment_yaml

runner = CliRunner()

_FAKE_JUDGE = {
    "model": "fake/deterministic-2026-01-01",
    "rubric": "rubric.md",
    "orders": "both",
    "temperature": 0,
}


def _setup_judged(expdir, *, tasks=None):
    """Plan, seed two arms' graded trials, and judge — leaving verdicts to build
    a review packet from."""
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE))
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    tasks = tasks or [{"id": "t1", "prompt": "solve it", "task_class": "refactor"}]
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    ctx = fixed_ctx(experiment_id="exp")
    for t in tasks:
        seed_trial_and_grade(ledger, ctx, trial_id=f"a-{t['id']}", task_id=t["id"],
                             arm="control", passed=True)
        seed_trial_and_grade(ledger, ctx, trial_id=f"b-{t['id']}", task_id=t["id"],
                             arm="treatment", passed=False)
    assert runner.invoke(app, ["judge", str(expdir)]).exit_code == 0
    return ledger


def test_rv3_review_build_records_response_map(tmp_path):
    expdir = tmp_path / "exp"
    ledger = _setup_judged(expdir)
    r = runner.invoke(app, ["review", "build", str(expdir)])
    assert r.exit_code == 0, r.output

    built = find_events(ledger, "review_packet_built")
    assert len(built) == 1
    ev = built[0]
    assert ev["comparison_id"] == "cmp-t1-r0"
    assert ev["task_id"] == "t1"
    assert ev["task_class"] == "refactor"
    # the response map names the two arms, one per column
    assert set(ev["response_map"].keys()) == {"1", "2"}
    assert set(ev["response_map"].values()) == {"control", "treatment"}

    # the offline packet was written and presents blinded Response 1/2 columns
    packet = (expdir / "review_packet.html").read_text(encoding="utf-8")
    assert packet.startswith("<!doctype html>")
    assert "Response 1" in packet and "Response 2" in packet
    # no arm identity leaks into the shipped packet
    assert "control" not in packet and "treatment" not in packet
    assert runner.invoke(app, ["verify-chain", str(ledger)]).exit_code == 0


def test_rv3_review_build_is_idempotent(tmp_path):
    """Re-running `review build` appends zero new packet events and re-renders
    a byte-identical packet (7A-4).

    Build re-recorded review_packet_built per comparison; a second run doubled
    the packet events. The re-run reuses the ledgered response_map, so the
    rendered packet matches the ledgered blinding state exactly.
    """
    expdir = tmp_path / "exp"
    ledger = _setup_judged(expdir)

    r1 = runner.invoke(app, ["review", "build", str(expdir)])
    assert r1.exit_code == 0, r1.output
    built_first = find_events(ledger, "review_packet_built")
    assert len(built_first) == 1
    packet_first = (expdir / "review_packet.html").read_text(encoding="utf-8")

    r2 = runner.invoke(app, ["review", "build", str(expdir)])
    assert r2.exit_code == 0, r2.output
    built_second = find_events(ledger, "review_packet_built")
    assert len(built_second) == 1  # zero new packet events
    packet_second = (expdir / "review_packet.html").read_text(encoding="utf-8")
    assert packet_second == packet_first  # byte-identical re-render


def test_rv2_rv6_reveal_and_guess_from_recorded_map(tmp_path):
    """Reveal discloses the REAL arm identities from the recorded map (not a
    hardcoded arm_a/arm_b), and actual_arm is supplied so guess accuracy is a
    measured number, not a structural 0.0 (RV-2, RV-6)."""
    expdir = tmp_path / "exp"
    ledger = _setup_judged(expdir)
    assert runner.invoke(app, ["review", "build", str(expdir)]).exit_code == 0
    built = find_events(ledger, "review_packet_built")[0]
    resp1_arm = built["response_map"]["1"]  # the true arm shown as Response 1

    # reviewer recognizes the arm and correctly guesses Response 1's arm
    r = runner.invoke(app, [
        "review", "record", str(expdir), "--comparison-id", "cmp-t1-r0",
        "--winner", "1", "--arm-recognized", "--arm-guess", resp1_arm,
    ])
    assert r.exit_code == 0, r.output
    hv = find_events(ledger, "human_verdict")[0]
    # actual_arm now comes from the recorded map (was never supplied before -> 0.0)
    assert hv["integrity"]["actual_arm"] == resp1_arm
    assert hv["integrity"]["arm_guess"] == resp1_arm  # measured: a correct guess
    # the human's response pick was translated to the judge's A/B (arm) frame
    assert hv["verdict"]["winner"] == ("A" if resp1_arm == "control" else "B")
    assert hv["verdict"]["task_class"] == "refactor"

    # reveal discloses the REAL identities (the recorded map)
    r2 = runner.invoke(app, ["review", "reveal", str(expdir), "--comparison-id", "cmp-t1-r0"])
    assert r2.exit_code == 0, r2.output
    rev = find_events(ledger, "reveal")[0]
    assert rev["revealed"]["arm_identities"] == built["response_map"]
    assert set(rev["revealed"]["arm_identities"].values()) == {"control", "treatment"}


def test_rv6_record_refused_without_build(tmp_path):
    """A verdict for a comparison with no recorded packet map is refused — the
    map is required to translate the response pick and supply actual_arm."""
    expdir = tmp_path / "exp"
    _setup_judged(expdir)  # judged, but review build NOT run
    r = runner.invoke(app, [
        "review", "record", str(expdir), "--comparison-id", "cmp-t1-r0", "--winner", "1",
    ])
    assert r.exit_code == 2
    assert "review build" in r.output


def test_rv3_response_order_randomized_per_comparison():
    """The per-comparison Response-1/2 order is deterministic in (seed,
    comparison_id) and varies across comparisons, so no arm sits consistently in
    one column."""
    from harness.review.build import _swap

    outcomes = {_swap(1234, f"cmp-t{i}-r0") for i in range(30)}
    assert outcomes == {True, False}  # both orders occur across comparisons
    # deterministic for a fixed (seed, id)
    assert _swap(1234, "cmp-t3-r0") == _swap(1234, "cmp-t3-r0")
