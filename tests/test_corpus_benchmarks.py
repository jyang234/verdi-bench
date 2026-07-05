"""Standardized public-benchmark import + materialization [EVAL-8 §M1].

Proves the "plug verdi into a respected task set" claim with an enforcing test:
a SWE-bench instances export maps into citable, admitted corpus tasks, and
materialization produces a runnable experiment whose agent-visible tasks.yaml
never carries the benchmark's own grading tests (insulation).
"""

from __future__ import annotations

import json

import pytest
import yaml

from harness.corpus.benchmarks import (
    BenchmarkRecordError,
    SweBenchSource,
    swebench_holdout_results,
    swebench_task_content,
)
from harness.corpus.commit import load_task_dicts
from harness.corpus.materialize import agent_visible_leak, materialize_experiment
from harness.corpus.public import import_public_dataset
from harness.corpus.registry import CorpusManifest
from tests.fixtures.docker import DOCKER_AVAILABLE

# A representative SWE-bench Verified record (fields as the HF dataset ships them:
# FAIL_TO_PASS / PASS_TO_PASS are JSON-encoded strings).
_INSTANCE = {
    "instance_id": "astropy__astropy-12345",
    "repo": "astropy/astropy",
    "base_commit": "abc123",
    "problem_statement": "Fix the units regression in `Quantity.__mul__`.",
    "patch": "diff --git a/astropy/units.py ...",  # gold solution — must NEVER surface
    "test_patch": "diff --git a/astropy/tests/test_units.py ... SECRETHOLDOUT",
    "FAIL_TO_PASS": json.dumps(["astropy/tests/test_units.py::test_mul_regression"]),
    "PASS_TO_PASS": json.dumps(["astropy/tests/test_units.py::test_add"]),
    "version": "5.1",
    "created_at": "2023-04-01T00:00:00Z",
    "environment_setup_commit": "def456",
}


def _write_export(path, records, *, jsonl=True):
    if jsonl:
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(records), encoding="utf-8")
    return path


# --- the pure mapping -------------------------------------------------------
def test_mapping_separates_prompt_from_grading_tests():
    content = swebench_task_content(_INSTANCE)
    assert content["id"] == "astropy__astropy-12345"
    assert content["prompt"] == _INSTANCE["problem_statement"]
    # tests live under the holdout key, normalized from JSON strings to lists
    assert content["holdout"]["fail_to_pass"] == [
        "astropy/tests/test_units.py::test_mul_regression"
    ]
    assert content["holdout"]["pass_to_pass"] == ["astropy/tests/test_units.py::test_add"]
    assert content["holdout"]["test_patch"] == _INSTANCE["test_patch"]
    # the agent-visible surface (everything but the holdout) carries neither the
    # tests nor the gold patch
    visible = {k: v for k, v in content.items() if k != "holdout"}
    blob = json.dumps(visible)
    assert "SECRETHOLDOUT" not in blob
    assert _INSTANCE["patch"] not in blob


def test_mapping_refuses_a_record_missing_a_required_field():
    bad = {k: v for k, v in _INSTANCE.items() if k != "FAIL_TO_PASS"}
    with pytest.raises(BenchmarkRecordError):
        swebench_task_content(bad)


def test_mapping_refuses_malformed_test_list():
    bad = dict(_INSTANCE, FAIL_TO_PASS="not-json-not-a-list")
    with pytest.raises(BenchmarkRecordError):
        swebench_task_content(bad)


def test_mapping_refuses_record_without_instance_id():
    """A record with no instance_id/id has no citable identity — refuse loudly
    rather than import it as the literal '<unknown>'."""
    bad = {k: v for k, v in _INSTANCE.items() if k != "instance_id"}
    with pytest.raises(BenchmarkRecordError, match="instance_id"):
        swebench_task_content(bad)


def test_source_refuses_non_object_array_element(tmp_path):
    p = _write_export(tmp_path / "arr.json", [_INSTANCE, 42], jsonl=False)
    with pytest.raises(BenchmarkRecordError, match="not a JSON object"):
        SweBenchSource(p).fetch()


# --- the import (citable, admitted, dated) ----------------------------------
def test_swebench_import_yields_citable_admitted_dated_tasks(tmp_path):
    export = _write_export(tmp_path / "instances.jsonl", [_INSTANCE])
    cache = tmp_path / "cache"
    manifest = import_public_dataset(
        SweBenchSource(export), cache, corpus_id="swe-bench", dataset_name="swe-bench"
    )
    assert manifest.dataset.name == "swe-bench"
    entry = manifest.task("astropy__astropy-12345")
    assert entry is not None
    assert entry.status == "admitted"            # public tasks import admitted
    assert entry.sha and len(entry.sha) == 64    # a citable identity
    assert entry.created_at == "2023-04-01T00:00:00Z"  # feeds contamination dating
    assert entry.metadata["repo"] == "astropy/astropy"


def test_swebench_import_is_idempotent(tmp_path):
    export = _write_export(tmp_path / "instances.json", [_INSTANCE], jsonl=False)
    cache = tmp_path / "cache"
    m1 = import_public_dataset(SweBenchSource(export), cache, corpus_id="swe-bench")
    m2 = import_public_dataset(SweBenchSource(export), cache, corpus_id="swe-bench")
    assert m1.task_shas() == m2.task_shas()
    assert (cache / "manifest.json").read_text() == m1.to_json() + "\n"


def test_duplicate_instance_id_is_refused(tmp_path):
    export = _write_export(tmp_path / "dup.jsonl", [_INSTANCE, _INSTANCE])
    with pytest.raises(BenchmarkRecordError):
        SweBenchSource(export).fetch()


# --- materialization (runnable + insulated) ---------------------------------
def test_materialize_is_runnable_and_hides_the_tests(tmp_path):
    export = _write_export(tmp_path / "instances.jsonl", [_INSTANCE])
    cache = tmp_path / "cache"
    manifest = import_public_dataset(SweBenchSource(export), cache, corpus_id="swe-bench")

    expdir = tmp_path / "exp"
    materialize_experiment(manifest, cache, expdir)

    # tasks.yaml is well-formed and round-trips through the loader run/plan use
    task_dicts = load_task_dicts(expdir)
    assert len(task_dicts) == 1
    t = task_dicts[0]
    assert t["id"] == "astropy__astropy-12345"
    assert t["prompt"] == _INSTANCE["problem_statement"]
    assert t["holdouts_dir"] == "holdouts/astropy__astropy-12345"
    assert t["image"].endswith("astropy__astropy-12345:latest")

    # INSULATION: the benchmark's own tests + gold patch are in the read-only
    # holdouts dir, and NOWHERE in the agent-visible tasks.yaml
    tasks_text = (expdir / "tasks.yaml").read_text(encoding="utf-8")
    assert agent_visible_leak(tasks_text, ["SECRETHOLDOUT", _INSTANCE["patch"]]) is None
    holdout = json.loads(
        (expdir / "holdouts" / "astropy__astropy-12345" / "holdout.json").read_text()
    )
    assert holdout["kind"] == "swe-bench"
    assert "SECRETHOLDOUT" in holdout["test_patch"]  # the tests ARE captured, just insulated


def test_materialized_experiment_locks(tmp_path):
    """A materialized SWE-bench experiment is a valid pre-registration that
    `bench plan` can lock (the tasks.yaml the loader emits is spec-shaped)."""
    export = _write_export(tmp_path / "instances.jsonl", [_INSTANCE])
    cache = tmp_path / "cache"
    manifest = import_public_dataset(SweBenchSource(export), cache, corpus_id="swe-bench")

    expdir = tmp_path / "exp"
    materialize_experiment(manifest, cache, expdir)
    # add the pre-registration files the lock needs (arms/judge/rubric)
    from tests.fixtures.builders import write_experiment_yaml

    write_experiment_yaml(expdir / "experiment.yaml", repetitions=1)
    from harness.corpus.commit import load_task_dicts as _load
    from harness.plan.lock import lock_experiment
    from tests.fixtures.builders import fixed_ctx

    outcome = lock_experiment(
        expdir / "experiment.yaml",
        expdir / "ledger.ndjson",
        ctx=fixed_ctx(),
        task_dicts=_load(expdir),
    )
    assert outcome.spec_sha256  # locked cleanly over the materialized tasks


# --- the grading contract, and the whole pipeline ---------------------------
def test_swebench_holdout_spec_grades_through_the_real_grader():
    """The materialized SWE-bench holdout spec → reference results → the ACTUAL
    deterministic grader parser + scorer. Proves the holdout FORMAT is what the
    grader consumes, and that 'resolved' maps to a passing binary score."""
    from harness.grade.deterministic import compute_binary_score, parse_holdout_output

    spec = swebench_task_content(_INSTANCE)["holdout"]
    ftp = spec["fail_to_pass"][0]

    resolved = swebench_holdout_results(spec, {t: True for t in spec["fail_to_pass"] + spec["pass_to_pass"]})
    assert compute_binary_score(parse_holdout_output(resolved)) is True

    # a single still-failing FAIL_TO_PASS test ⇒ not resolved ⇒ fail
    unresolved_map = {t: True for t in spec["fail_to_pass"] + spec["pass_to_pass"]}
    unresolved_map[ftp] = False
    unresolved = swebench_holdout_results(spec, unresolved_map)
    assert compute_binary_score(parse_holdout_output(unresolved)) is False


def test_swebench_corpus_runs_end_to_end_through_bench(tmp_path):
    """AUTHORITATIVE compatibility proof: a materialized SWE-bench corpus flows
    through the real bench pipeline — plan → run (fake engine) → grade (local) →
    analyze → verify-chain — and the grader consumes SWE-bench-derived holdout
    results, producing the expected per-arm scores. The ONLY simulated step is
    the SWE-bench test *execution* (its own image/harness); every verdi seam is
    exercised for real via the CLI."""
    import json as _json
    from pathlib import Path as _Path

    from typer.testing import CliRunner

    from harness.cli import app
    from harness.ledger.query import find_events
    from tests.fixtures.builders import write_experiment_yaml

    runner = CliRunner()

    def _ok(*args):
        r = runner.invoke(app, [str(a) for a in args])
        assert r.exit_code == 0, f"{args}\n{r.output}"
        return r

    inst2 = dict(_INSTANCE, instance_id="flask__flask-9",
                 problem_statement="Fix the routing bug.", repo="pallets/flask")
    export = _write_export(tmp_path / "instances.jsonl", [_INSTANCE, inst2])
    cache = tmp_path / "cache"
    manifest = import_public_dataset(SweBenchSource(export), cache, corpus_id="swe-bench")

    expdir = tmp_path / "exp"
    materialize_experiment(manifest, cache, expdir)
    manifest.save(expdir / "manifest.json")  # for the run admission gate
    # a deterministic fake judge + single repetition (real arms/rubric)
    write_experiment_yaml(
        expdir / "experiment.yaml", repetitions=1,
        judge={"model": "fake/deterministic-2026-01-01", "rubric": "rubric.md",
               "orders": "both", "temperature": 0},
    )
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    ledger = expdir / "ledger.ndjson"

    # plan → run(fake), gated on the imported (admitted) SWE-bench manifest
    _ok("plan", expdir / "experiment.yaml", "--ledger", ledger)
    _ok("run", expdir, "--corpus-manifest", expdir / "manifest.json")

    trials = {ev["trial_record"]["trial_id"]: ev["trial_record"]
              for ev in find_events(ledger, "trial")}
    assert trials, "no trials ran on the materialized SWE-bench tasks"
    assert {r["task_id"] for r in trials.values()} == {"astropy__astropy-12345", "flask__flask-9"}

    # stand in for the SWE-bench grading image: run its tests → holdout_results.json
    # via the reference shim. control resolves both instances; treatment fails one.
    for rec in trials.values():
        ws = _Path(rec["artifacts_path"]).parent
        spec = _json.loads(
            (expdir / "holdouts" / rec["task_id"] / "holdout.json").read_text()
        )
        all_ids = [*spec["fail_to_pass"], *spec["pass_to_pass"]]
        outcomes = {t: True for t in all_ids}
        if rec["arm"] == "treatment":
            outcomes[spec["fail_to_pass"][0]] = False  # treatment did not resolve it
        (ws / "holdout_results.json").write_text(
            _json.dumps(swebench_holdout_results(spec, outcomes)), encoding="utf-8"
        )

    _ok("grade", expdir, "--runner", "local")

    # the grader consumed the SWE-bench-derived results: control passed, treatment failed
    scores = {}
    for ev in find_events(ledger, "grade"):
        arm = trials[ev["trial_id"]]["arm"]
        scores.setdefault(arm, []).append(ev["binary_score"])
    assert scores.get("control") and all(scores["control"]), scores
    assert scores.get("treatment") and not any(scores["treatment"]), scores

    # analyze completes on the SWE-bench corpus, and the chain verifies
    _ok("analyze", expdir, "--exploratory")
    assert (expdir / "findings.exploratory.md").exists()
    _ok("verify-chain", ledger)


@pytest.mark.docker
@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_docker_grade_of_materialized_swebench_task(tmp_path):
    """The final seal (docker-marked, CI only): verdi's REAL trusted grading
    container grades a materialized SWE-bench task.

    The grading image is a busybox stand-in for SWE-bench's per-instance image —
    a drop-in for the one entrypoint that runs the instance's baked-in tests. It
    *requires* the materialized holdout spec to have been mounted read-only
    before it emits the resolved results, so this proves verdi delivered the
    SWE-bench holdout to the network-less grading container and scored its
    FAIL_TO_PASS/PASS_TO_PASS results at the trusted (``grader=docker``) tier —
    the one step the offline pipeline test simulates on the host."""
    import subprocess

    from harness.grade.container import DockerGradeRunner, GradingContainer
    from harness.grade.deterministic import grade_trial
    from harness.grade.types import GradeTask
    from harness.ledger.events import EventContext
    from harness.ledger.query import find_events

    # import + materialize a real SWE-bench instance (host side)
    export = _write_export(tmp_path / "instances.jsonl", [_INSTANCE])
    cache = tmp_path / "cache"
    manifest = import_public_dataset(SweBenchSource(export), cache, corpus_id="swe-bench")
    expdir = tmp_path / "exp"
    materialize_experiment(manifest, cache, expdir)
    task_id = "astropy__astropy-12345"
    holdouts_dir = expdir / "holdouts" / task_id
    spec = json.loads((holdouts_dir / "holdout.json").read_text())

    # what SWE-bench's image emits when the instance is resolved — via the REAL
    # reference function, from the REAL materialized spec.
    resolved = swebench_holdout_results(
        spec, {t: True for t in spec["fail_to_pass"] + spec["pass_to_pass"]}
    )

    # a tiny grader image that FAILS unless verdi mounted the holdout spec at
    # /holdouts (read-only), then emits the resolved results on the
    # nonce-authenticated fenced stdout transport [F-H1] — the host reads no
    # workspace file, and the grader stamps the per-run VERDI_FENCE_NONCE into
    # its fence marker (shell expands ${VERDI_FENCE_NONCE} at runtime).
    from harness.grade.container import holdout_fence

    begin, end = holdout_fence("${VERDI_FENCE_NONCE}")

    img = tmp_path / "img"
    img.mkdir()
    (img / "results.json").write_text(json.dumps(resolved), encoding="utf-8")
    (img / "Dockerfile").write_text(
        "FROM busybox\n"
        "COPY results.json /results.json\n"
        'CMD ["sh", "-c", "test -f /holdouts/holdout.json && '
        f'echo {begin} && cat /results.json && echo {end}"]\n',
        encoding="utf-8",
    )
    image = "verdi-bench/swebench-grader-e2e:latest"
    subprocess.run(["docker", "build", "-t", image, str(img)], check=True, capture_output=True)

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "patch_applied.txt").write_text("agent solution", encoding="utf-8")

    ledger = tmp_path / "l.ndjson"
    container = GradingContainer(runner=DockerGradeRunner(), image=image)
    grade_trial(
        "trial-swe",
        GradeTask(id=task_id, task_sha="s", holdouts_dir=str(holdouts_dir)),
        ws, ledger, EventContext(experiment_id="e", clock=lambda: "t"), container=container,
    )

    grades = find_events(ledger, "grade")
    assert len(grades) == 1
    g = grades[0]
    assert g["binary_score"] is True             # the instance resolved
    assert g.get("grader") == "docker"           # trusted tier, not local/advisory
    graded_ids = {a["id"] for a in g["assertions"]}
    assert set(spec["fail_to_pass"]) <= graded_ids   # SWE-bench tests were the ones scored
    assert set(spec["pass_to_pass"]) <= graded_ids
