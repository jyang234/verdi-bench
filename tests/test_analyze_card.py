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

from harness.analyze.card import CardError, build_card, compare_cards, serialize_card
from harness.cli import app
from harness.ledger.query import find_events
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import write_experiment_yaml

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
        (ws / "holdout_results.json").write_text(
            json.dumps({"assertions": [{"id": "h1", "result": "pass" if passed else "fail"}]}),
            encoding="utf-8",
        )
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
        (ws / "holdout_results.json").write_text(
            json.dumps({"assertions": [{"id": "t::a", "result": "pass" if passed else "fail"}]}),
            encoding="utf-8",
        )
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
