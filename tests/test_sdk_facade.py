"""SDK facade behaviors: fail-loud refusals, pre-lock guards, seams [refactor 02 §5].

The workspace facade owns exactly one piece of behavior beyond delegation —
turning the stage APIs' *outcome-flag* refusals into typed exceptions so a
library caller cannot silently proceed past a refusal. These pin that, plus the
builder's pre-lock guard, the documented Phase-3 seams, the env gate, the fake
operator injection, and the ``bench init`` scaffold.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from harness.sdk import (
    ContaminationProbeRefusal,
    CorpusAdmitPersistError,
    Experiment,
    ExperimentWorkspace,
    MissingEnvKeysError,
    RunQuarantineRefusal,
    Task,
    require_env_keys,
    write_holdout_results,
)


def _mini(tmp_path):
    return (
        Experiment("mini", seed=1234, cost_ceiling_usd=10.0)
        .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
        .arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
        .judge("fake/deterministic-2026-01-01")
        .task(Task("t1", prompt="p", fake_behavior={"native_log": {"total_cost_usd": 0.01}}))
    )


# --- fail-loud: outcome flags become typed exceptions -------------------------
def test_run_raises_on_quarantine_flag(tmp_path, monkeypatch):
    from harness.run import api as run_api

    def fake_run(*a, **k):
        return run_api.RunOutcome(
            n_trials=0, infra_failures=0, stopped_cost_ceiling=False,
            aborted_proxy_unavailable=False, quarantine_error="task t1 quarantined",
        )

    monkeypatch.setattr(run_api, "run_experiment", fake_run)
    with pytest.raises(RunQuarantineRefusal, match="quarantined"):
        ExperimentWorkspace(tmp_path).run(engine="fake")


def test_contamination_probe_raises_on_probe_error(tmp_path, monkeypatch):
    from harness.contamination import api as c_api

    def fake_probe(*a, **k):
        return c_api.ContaminationProbeOutcome(probe=None, probe_error="broken holdout layout")

    monkeypatch.setattr(c_api, "contamination_probe", fake_probe)
    with pytest.raises(ContaminationProbeRefusal, match="broken holdout"):
        ExperimentWorkspace(tmp_path).contamination_probe(actor="tester")


def test_corpus_admit_raises_on_persist_error(tmp_path, monkeypatch):
    from harness.corpus import api as corpus_api

    def fake_admit(*a, **k):
        return corpus_api.AdmitOutcome(persist_error="manifest save failed; re-save to reconcile")

    monkeypatch.setattr(corpus_api, "corpus_admit", fake_admit)
    with pytest.raises(CorpusAdmitPersistError, match="reconcile"):
        ExperimentWorkspace(tmp_path).corpus_admit(
            manifest_path=tmp_path / "m.json", candidate_id="c", task_sha="s",
            baseline_ref="b", keyring=tmp_path / "k", actor="tester",
        )


# --- write path guards + Phase-3 seams ---------------------------------------
def test_write_refuses_a_dir_with_a_ledger(tmp_path):
    d = tmp_path / "exp"
    d.mkdir()
    (d / "ledger.ndjson").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="pre-lock"):
        _mini(tmp_path).write(d)


def test_task_holdout_slot_compiles_inline_and_fails_loud_for_garbage(tmp_path):
    """The inline ``holdout=`` seam is wired (refactor 05 §1): a valid Holdout
    compiles to ``holdouts/<id>/`` + ``holdouts_dir`` at write time; a non-holdout
    value fails loudly rather than silently dropping the grading contract."""
    from harness.grade.holdouts import AssertionHoldout

    exp = _mini_dir_agnostic()
    exp._tasks[0] = Task("t1", prompt="p", holdout=AssertionHoldout(expression="assert True"))
    exp.write(tmp_path / "ok")
    assert (tmp_path / "ok" / "holdouts" / "t1" / "holdout.json").exists()
    # the inline object is compiled OUT — never serialized into tasks.yaml
    assert "holdout:" not in (tmp_path / "ok" / "tasks.yaml").read_text()

    exp2 = _mini_dir_agnostic()
    exp2._tasks[0] = Task("t1", prompt="p", holdout=object())
    with pytest.raises(Exception):  # not a valid Holdout — fail loudly, not silent
        exp2.write(tmp_path / "bad")


def test_arm_image_is_a_documented_phase3_seam():
    with pytest.raises(NotImplementedError, match="Phase-3"):
        Experiment("x", seed=1, cost_ceiling_usd=1.0).arm(
            "a", model="p/m", image="official:generic-llm"
        )


def _mini_dir_agnostic():
    return (
        Experiment("mini", seed=1234, cost_ceiling_usd=10.0)
        .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
        .arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
        .judge("fake/deterministic-2026-01-01")
        .task(Task("t1", prompt="p"))
    )


# --- fake-path operator injection --------------------------------------------
def test_inject_holdout_results_writes_per_trial(tmp_path):
    import json
    from pathlib import Path

    ws = _mini(tmp_path).repetitions(1).write(tmp_path / "exp")
    ws.plan(actor="tester")
    ws.run(engine="fake")
    n = ws.inject_holdout_results(lambda arm, task: arm == "treatment")
    assert n == 2  # 1 task x 2 arms
    seen = {}
    for tv in ws.view().trials():
        rec = tv.record
        p = Path(rec["artifacts_path"]).parent / "holdout_results.json"
        seen[rec["arm"]] = json.loads(p.read_text(encoding="utf-8"))["assertions"][0]["result"]
    assert seen == {"treatment": "pass", "control": "fail"}


def test_write_holdout_results_shape(tmp_path):
    payload = write_holdout_results(tmp_path, True, assertion_id="hX")
    assert payload == {"assertions": [{"id": "hX", "result": "pass"}]}


# --- env gate -----------------------------------------------------------------
def test_require_env_keys_names_every_missing_key():
    with pytest.raises(MissingEnvKeysError) as ei:
        require_env_keys("A_KEY", "B_KEY", env={"A_KEY": "set", "B_KEY": ""})
    # empty counts as missing; only B is named
    assert ei.value.missing == ["B_KEY"]
    assert require_env_keys("A_KEY", env={"A_KEY": "v"}) == {"A_KEY": "v"}


# --- bench init scaffold ------------------------------------------------------
def test_bench_init_scaffolds_and_refuses_non_empty(tmp_path):
    from harness.cli import app

    runner = CliRunner()
    target = tmp_path / "myexp"
    r = runner.invoke(app, ["init", str(target)])
    assert r.exit_code == 0, r.output
    assert (target / "experiment.yaml").exists()
    assert (target / "tasks.yaml").exists()
    # the rubric lands where the spec points, so the scaffold is self-consistent
    assert (target / "rubrics" / "code-task-v1.md").exists()

    # refuses a non-empty target rather than clobbering
    r2 = runner.invoke(app, ["init", str(target)])
    assert r2.exit_code == 2
    assert "not empty" in r2.output
