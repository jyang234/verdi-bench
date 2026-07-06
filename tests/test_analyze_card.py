"""The benchmark result card — comparability & legibility layer.

Design: docs/design/review/verdi-bench-result-card-design.md. The card is a
read-only projection that makes a run citable (tamper-evident provenance) and
comparable (a verifiable battery_sha), co-equal in score and paired delta.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from harness.analyze.card import (
    CardError,
    build_card,
    compare_cards,
    render_card_html,
    render_card_markdown,
    serialize_card,
)
from harness.cli import app
from harness.ledger.query import find_events
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import write_experiment_yaml
from tests.fixtures.grading import write_holdout_results

runner = CliRunner()

_FAKE_JUDGE = {"model": "fake/deterministic-2026-01-01", "rubric": "rubric.md",
               "orders": "both", "temperature": 0}


def _ok(*args):
    r = runner.invoke(app, [str(a) for a in args])
    assert r.exit_code == 0, f"{args}\n{r.output}"
    return r


def _graded_analyzed(tmp_path, *, name="exp", prompt="solve it",
                     control_pass=True, treatment_pass=False):
    """plan → run (fake) → grade (local) → analyze, returning (expdir, spec)."""
    expdir = tmp_path / name
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE), repetitions=1)
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": prompt}]}), encoding="utf-8"
    )
    ledger = expdir / "ledger.ndjson"
    _ok("plan", expdir / "experiment.yaml", "--ledger", ledger)
    _ok("run", expdir)
    for ev in find_events(ledger, "trial"):
        rec = ev["trial_record"]
        ws = Path(rec["artifacts_path"]).parent
        passed = control_pass if rec["arm"] == "control" else treatment_pass
        write_holdout_results(ws, passed)
    _ok("grade", expdir, "--runner", "local")
    _ok("analyze", expdir, "--exploratory")
    spec = ExperimentSpec.from_yaml(expdir / "experiment.yaml")
    return expdir, spec


def _card(expdir, spec, **kw):
    task_ids = [t["id"] for t in yaml.safe_load((expdir / "tasks.yaml").read_text())["tasks"]]
    return build_card(expdir / "ledger.ndjson", spec, task_ids=task_ids, **kw)


# --- co-equal score + delta, provenance, honesty ---------------------------
def test_card_carries_absolute_score_and_paired_delta_co_equal(tmp_path):
    expdir, spec = _graded_analyzed(tmp_path, control_pass=True, treatment_pass=False)
    card = _card(expdir, spec)

    # the leaderboard's language: per-arm absolute score (control resolved, treatment did not)
    by_arm = {a["name"]: a for a in card["arms"]}
    assert by_arm["control"]["absolute_score"] == 1.0
    assert by_arm["treatment"]["absolute_score"] == 0.0
    assert by_arm["control"]["n"] == 1

    # verdi's rigor, co-equal: the paired delta + CI are present
    comp = card["comparison"]
    assert comp["arm_a"] == "control" and comp["arm_b"] == "treatment"
    assert comp["delta"] == 1.0 and "ci_low" in comp and "ci_method" in comp

    # honesty stamps + tamper-evident provenance
    assert card["instrument"]["tier"] == "ADVISORY"
    assert card["provenance"]["mode"] == "exploratory"
    assert card["provenance"]["spec_sha256"] and card["provenance"]["ledger_head"]
    assert card["battery"]["battery_sha"]


def test_card_is_byte_deterministic(tmp_path):
    expdir, spec = _graded_analyzed(tmp_path)
    assert serialize_card(_card(expdir, spec)) == serialize_card(_card(expdir, spec))


def test_exploratory_card_never_claims_official(tmp_path):
    """An exploratory render must not present a fenced 'official' decision — the
    card carries the multi-arm primary-pair flag, not a fence result, and the
    renders qualify the decision as exploratory."""
    expdir, spec = _graded_analyzed(tmp_path)  # exploratory
    card = _card(expdir, spec)
    assert "official_decision" not in card["comparison"]     # the misleading field is gone
    assert card["comparison"]["is_primary_pair"] is True
    md = render_card_markdown(card)
    assert "official: True" not in md
    assert "An official, fenced finding requires" in md      # the watermark note
    assert "official True" not in render_card_html(card)


def test_card_discloses_forensic_quarantines(tmp_path):
    expdir, spec = _graded_analyzed(tmp_path)
    d = _card(expdir, spec)["disclosures"]
    assert d["forensic_quarantines"] == []                   # none here, but the key is present


def _append_post_render_grade(ledger):
    """Simulate a post-render re-grade — the F-H5 staleness scenario."""
    from harness.ledger.events import EventContext, record_grade

    g = find_events(ledger, "grade")[-1]
    record_grade(
        ledger, EventContext(experiment_id="exp", actor="tester"),
        trial_id=g["trial_id"], task_sha=g["task_sha"],
        assertions=[{"id": "h1", "source": "holdout_test", "result": "pass", "detail": None}],
        binary_score=True, override_of="a" * 64,
    )


def test_h5_card_refuses_events_appended_after_render(tmp_path):
    """F-H5: the card certifies a rendered result — after any event is appended
    past the last findings render (quarantine, re-grade, …), emitting a card
    must refuse rather than stamp recomputed numbers with the stale mode."""
    expdir, spec = _graded_analyzed(tmp_path)
    _append_post_render_grade(expdir / "ledger.ndjson")
    with pytest.raises(CardError, match="re-run `bench analyze`"):
        _card(expdir, spec)


def test_h5_card_carries_render_binding(tmp_path):
    """F-H5: the card binds to the render event it certifies, so a third party
    can check the card against the chain without recomputing."""
    expdir, spec = _graded_analyzed(tmp_path)
    card = _card(expdir, spec)
    rendered = find_events(expdir / "ledger.ndjson", "findings_rendered")[-1]
    assert card["provenance"]["rendered_head_hash"] == rendered["rendered_head_hash"]
    assert card["provenance"]["findings_sha256"] == rendered["findings_sha256"]
    assert card["schema_version"] == 2


def test_h5_card_refuses_broken_chain(tmp_path):
    """F-H5: parity with the render path's _assert_head_hash — a card is never
    projected from a ledger whose chain does not verify."""
    expdir, spec = _graded_analyzed(tmp_path)
    ledger = expdir / "ledger.ndjson"
    data = ledger.read_text(encoding="utf-8")
    assert '"binary_score":true' in data
    ledger.write_text(data.replace('"binary_score":true', '"binary_score":false', 1),
                      encoding="utf-8")
    with pytest.raises(CardError, match="chain"):
        _card(expdir, spec)


def test_h5_cli_emit_refuses_stale_render_exit_2(tmp_path):
    expdir, _ = _graded_analyzed(tmp_path)
    _append_post_render_grade(expdir / "ledger.ndjson")
    r = runner.invoke(app, ["card", "emit", str(expdir)])
    assert r.exit_code == 2
    assert "analyze" in r.output


def test_card_requires_a_prior_analyze(tmp_path):
    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE), repetitions=1)
    (expdir / "rubric.md").write_text("r", encoding="utf-8")
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8"
    )
    _ok("plan", expdir / "experiment.yaml", "--ledger", expdir / "ledger.ndjson")
    _ok("run", expdir)
    spec = ExperimentSpec.from_yaml(expdir / "experiment.yaml")
    with pytest.raises(CardError, match="analyze"):
        _card(expdir, spec)


# --- battery_sha: comparability is verifiable -------------------------------
def test_same_tasks_same_battery_changed_task_differs(tmp_path):
    e1, s1 = _graded_analyzed(tmp_path, name="a", prompt="solve it")
    e2, s2 = _graded_analyzed(tmp_path, name="b", prompt="solve it")          # identical tasks
    e3, s3 = _graded_analyzed(tmp_path, name="c", prompt="a DIFFERENT task")  # changed task

    b1 = _card(e1, s1)["battery"]["battery_sha"]
    b2 = _card(e2, s2)["battery"]["battery_sha"]
    b3 = _card(e3, s3)["battery"]["battery_sha"]
    assert b1 == b2          # same task set → comparable
    assert b1 != b3          # changed task content → different battery


# --- compare: side-by-side on match, loud refusal otherwise -----------------
def test_compare_matches_same_battery(tmp_path):
    e1, s1 = _graded_analyzed(tmp_path, name="a", control_pass=True, treatment_pass=False)
    e2, s2 = _graded_analyzed(tmp_path, name="b", control_pass=True, treatment_pass=True)
    result = compare_cards(_card(e1, s1), _card(e2, s2))
    assert result["comparable"] is True
    assert result["arms"]["a"]["treatment"]["absolute_score"] == 0.0
    assert result["arms"]["b"]["treatment"]["absolute_score"] == 1.0
    # the side-by-side carries the model, so unlike models under a shared arm
    # name are not silently compared
    assert result["arms"]["a"]["control"]["model"] == "anthropic/claude-haiku-4-5-20251001"


def test_compare_refuses_different_battery(tmp_path):
    e1, s1 = _graded_analyzed(tmp_path, name="a", prompt="task one")
    e2, s2 = _graded_analyzed(tmp_path, name="b", prompt="task two")
    with pytest.raises(CardError, match="not comparable.*different task set"):
        compare_cards(_card(e1, s1), _card(e2, s2))


def test_compare_refuses_different_metric(tmp_path):
    e1, s1 = _graded_analyzed(tmp_path, name="a")
    card_a = _card(e1, s1)
    card_b = json.loads(serialize_card(card_a))
    card_b["primary_metric"] = "cost_per_task"   # same tasks, different metric
    with pytest.raises(CardError, match="not comparable.*different primary metric"):
        compare_cards(card_a, card_b)


# --- the CLI + a SWE-bench battery identity ---------------------------------
def test_cli_emit_and_compare_roundtrip(tmp_path):
    e1, _ = _graded_analyzed(tmp_path, name="a", treatment_pass=False)
    e2, _ = _graded_analyzed(tmp_path, name="b", treatment_pass=True)
    _ok("card", "emit", e1, "--out", tmp_path / "a.json")
    _ok("card", "emit", e2, "--out", tmp_path / "b.json")
    r = _ok("card", "compare", tmp_path / "a.json", tmp_path / "b.json")
    assert '"comparable": true' in r.output


# --- human renders (slice 2) ------------------------------------------------
def test_markdown_render_is_deterministic_and_shows_score_and_delta(tmp_path):
    expdir, spec = _graded_analyzed(tmp_path, control_pass=True, treatment_pass=False)
    card = _card(expdir, spec)
    md = render_card_markdown(card)
    assert md == render_card_markdown(card)              # byte-deterministic
    # co-equal: both the per-arm absolute score AND the paired delta are present
    assert "absolute score" in md and "1.0000" in md
    assert "delta = 1.0000" in md
    # honesty stamps survive the render
    assert "Tier:** ADVISORY" in md and "battery_sha" in md
    assert "ADVISORY: " in md or "ADVISORY tier" in md


def test_html_render_is_self_contained_and_deterministic(tmp_path):
    expdir, spec = _graded_analyzed(tmp_path)
    card = _card(expdir, spec)
    one = render_card_html(card)
    assert one == render_card_html(card)
    # same archivability discipline as the dossier: no external/active references
    for needle in ("http://", "https://", "src=", "href=", "url(", "@import", "<script", "<link"):
        assert needle not in one, f"external/active reference {needle!r} in card html"
    assert "battery_sha" not in one or card["battery"]["battery_sha"] in one  # the sha itself is shown


def test_cli_emit_md_and_html(tmp_path):
    expdir, _ = _graded_analyzed(tmp_path)
    r_md = _ok("card", "emit", expdir, "--format", "md")
    assert "# verdi-bench result card" in r_md.output
    _ok("card", "emit", expdir, "--format", "html", "--out", tmp_path / "card.html")
    assert "<!doctype html>" in (tmp_path / "card.html").read_text()


def test_swebench_card_has_corpus_battery_and_resolved_rates(tmp_path):
    """A materialized SWE-bench run yields a card whose battery is anchored to the
    corpus (image-insensitive) and whose per-arm absolute is the resolved rate."""
    from harness.corpus.benchmarks import SweBenchSource
    from harness.corpus.materialize import materialize_experiment
    from harness.corpus.public import import_public_dataset

    instance = {
        "instance_id": "astropy__astropy-1", "repo": "astropy/astropy",
        "base_commit": "c0", "problem_statement": "Fix it.",
        "test_patch": "diff X", "FAIL_TO_PASS": json.dumps(["t::a"]),
        "PASS_TO_PASS": json.dumps([]), "version": "5.1",
        "created_at": "2023-01-01T00:00:00Z",
    }
    export = tmp_path / "instances.jsonl"
    export.write_text(json.dumps(instance) + "\n", encoding="utf-8")
    cache = tmp_path / "cache"
    manifest = import_public_dataset(
        SweBenchSource(export), cache, corpus_id="swe-bench", dataset_name="swe-bench"
    )

    expdir = tmp_path / "exp"
    materialize_experiment(manifest, cache, expdir)
    manifest.save(expdir / "manifest.json")
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_FAKE_JUDGE), repetitions=1)
    (expdir / "rubric.md").write_text("r", encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    _ok("plan", expdir / "experiment.yaml", "--ledger", ledger)
    _ok("run", expdir, "--corpus-manifest", expdir / "manifest.json")
    for ev in find_events(ledger, "trial"):
        rec = ev["trial_record"]
        ws = Path(rec["artifacts_path"]).parent
        passed = rec["arm"] == "control"
        write_holdout_results(ws, passed, assertion_id="t::a")
    _ok("grade", expdir, "--runner", "local")
    _ok("analyze", expdir, "--exploratory")

    spec = ExperimentSpec.from_yaml(expdir / "experiment.yaml")
    card = build_card(ledger, spec, task_ids=["astropy__astropy-1"], corpus_manifest=manifest)
    assert card["battery"]["battery_basis"] == "corpus"
    assert card["battery"]["corpus_id"] == "swe-bench"
    assert card["battery"]["dataset"]["name"] == "swe-bench"
    by_arm = {a["name"]: a for a in card["arms"]}
    assert by_arm["control"]["absolute_score"] == 1.0     # resolved
    assert by_arm["treatment"]["absolute_score"] == 0.0   # not resolved


def _with_asymmetric_contamination(card):
    """Inject the real asymmetry shape (contamination/summary.py:probe_asymmetries)
    into a built card — the renders are pure projections of the dict."""
    card["disclosures"]["contamination"] = {
        "probe_status": "complete",
        "asymmetric": [{"task_id": "t1", "flagged_arms": ["control"],
                        "unflagged_arms": ["treatment"]}],
    }
    return card


def test_m_o4_markdown_render_survives_asymmetric_contamination(tmp_path):
    """F-M-O4: `asymmetric` entries are dicts; the md render joined them as
    strings — a TypeError whenever any asymmetric flag existed. It now renders
    the same task/arm phrasing report.py uses."""
    expdir, spec = _graded_analyzed(tmp_path)
    md = render_card_markdown(_with_asymmetric_contamination(_card(expdir, spec)))
    assert "task 't1'" in md and "['control']" in md


def test_m_o5_html_card_carries_every_disclosure(tmp_path):
    """F-M-O5: the HTML card (the shareable artifact) dropped the entire
    Disclosures section the markdown card carries. Structural parity: every
    disclosure category renders in both."""
    expdir, spec = _graded_analyzed(tmp_path)
    card = _with_asymmetric_contamination(_card(expdir, spec))
    card["disclosures"]["forensic_quarantines"] = ["trial-q1"]
    html = render_card_html(card)
    md = render_card_markdown(card)
    assert "Disclosures" in html
    for needle in ("confounds", "task &#x27;t1&#x27;", "trial-q1", "excluded metrics"):
        assert needle in html, needle
    for needle in ("confounds", "task 't1'", "trial-q1", "excluded metrics"):
        assert needle in md, needle
