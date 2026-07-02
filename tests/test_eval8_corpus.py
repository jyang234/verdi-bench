"""EVAL-8 — corpus tooling: import, subset, mine, admission, boundary, versioning."""

from __future__ import annotations

import json

import pytest

from harness.corpus.admit import (
    BaselinePrerequisiteError,
    CurationRequiredError,
    admit_task,
)
from harness.corpus.mine import MergeRequest, MRFile, is_test_path, mine_mr
from harness.corpus.public import DirectorySource, import_terminal_bench
from pydantic import ValidationError

from harness.corpus.registry import (
    BoundaryViolationError,
    CorpusManifest,
    CorpusMutationError,
    Dataset,
    TaskEntry,
)
from harness.corpus.stratify import calibration_subset
from harness.ledger import events
from harness.ledger.events import record_curation_approval, record_flake_baseline
from tests.fixtures.builders import fixed_ctx


def _write_dataset(root, n=6):
    """Fabricate a small harbor-format dataset dir with category metadata."""
    root.mkdir(parents=True, exist_ok=True)
    cats = ["io", "io", "parsing", "parsing", "net", "net"]
    for i in range(n):
        (root / f"task-{i}.json").write_text(
            json.dumps({"id": f"task-{i}", "prompt": f"do thing {i}", "harbor": True}),
            encoding="utf-8",
        )
        (root / f"task-{i}.meta.json").write_text(
            json.dumps({"category": cats[i % len(cats)], "difficulty": "medium"}),
            encoding="utf-8",
        )
    return root


# --- AC-1: public import + idempotency --------------------------------------
def test_ac1_public_import_manifest(tmp_path):
    src = _write_dataset(tmp_path / "ds")
    cache = tmp_path / "cache"
    m1 = import_terminal_bench(DirectorySource(src), cache, dataset_version="2.0")
    assert m1.kind == "public"
    assert m1.dataset == Dataset(name="terminal-bench", version="2.0")
    assert len(m1.tasks) == 6
    assert all(t.format == "harbor" for t in m1.tasks)
    manifest_bytes = (cache / "manifest.json").read_bytes()

    # Re-import against the same dataset version is byte-identical: no duplicates,
    # no sha churn.
    m2 = import_terminal_bench(DirectorySource(src), cache, dataset_version="2.0")
    assert m2.to_json() == m1.to_json()
    assert (cache / "manifest.json").read_bytes() == manifest_bytes
    assert m1.task_shas() == m2.task_shas()


# --- AC-2: stratified selection, calibration status, official gate ----------
def test_ac2_stratified_selection(tmp_path):
    src = _write_dataset(tmp_path / "ds", n=6)
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")

    a = calibration_subset(manifest, seed=1234, target_size=3, stratum_key="category")
    # reproducible for a seed
    manifest2 = import_terminal_bench(DirectorySource(src), tmp_path / "cache2")
    b = calibration_subset(manifest2, seed=1234, target_size=3, stratum_key="category")
    assert a.task_ids == b.task_ids
    # a different seed generally yields a different draw
    c = calibration_subset(manifest2, seed=9999, target_size=3, stratum_key="category")
    assert a.strata["stratum_key"] == "category"
    # proportional allocation covers strata; 3 chosen from 3 strata of 2 each ⇒ 1 each
    assert len(a.task_ids) == 3
    assert sum(a.strata["allocation"].values()) == 3
    _ = c  # a different seed is exercised; equality is not asserted (may collide)


def test_ac2_calibration_status(tmp_path):
    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    manifest.calibration.status = "none"
    assert manifest.official_ready is False

    calibration_subset(manifest, seed=1, target_size=3)
    # selection alone does not validate
    assert manifest.calibration.status == "none"

    manifest.record_calibration_run({"anchor_delta": 0.02}, kind="subset")
    assert manifest.calibration.status == "subset-validated"
    assert manifest.official_ready is False

    manifest.record_calibration_run({"full": True}, kind="full")
    assert manifest.calibration.status == "full-run-validated"
    assert manifest.official_ready is True


def test_ac2_official_requires_full(tmp_path):
    # The manifest field the EVAL-6 official-render path checks.
    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    manifest.calibration.status = "subset-validated"
    assert manifest.official_ready is False
    manifest.calibration.status = "full-run-validated"
    assert manifest.official_ready is True


# --- AC-3: mining -----------------------------------------------------------
def test_ac3_mine_candidate():
    assert is_test_path("tests/test_foo.py")
    assert is_test_path("pkg/foo_test.go")
    assert not is_test_path("src/foo.py")

    mr = MergeRequest(
        parent_sha="abc123" * 6 + "dead",
        files=[
            MRFile(path="src/feature.py", change="added", content="def f(): ..."),
            MRFile(path="tests/test_feature.py", change="added", content="def test_f(): ..."),
            MRFile(path="tests/test_existing.py", change="modified", content="..."),
        ],
    )
    cand = mine_mr(mr, ticket_text="Implement feature X per the ticket.")
    assert cand.workspace_ref == mr.parent_sha
    assert cand.prompt == "Implement feature X per the ticket."
    assert cand.status == "pending-curation"
    # holdouts = added test files only (not the modified one, not the src file)
    assert [h["path"] for h in cand.holdouts] == ["tests/test_feature.py"]


# --- AC-4: admission gate ---------------------------------------------------
def _pending_manifest(candidate_id="cand-1", sha="s" * 64):
    return CorpusManifest(
        corpus_id="internal-koala",
        semver="1.0.0",
        kind="internal",
        boundary_path="/tmp/koala-boundary",
        tasks=[TaskEntry(task_id=candidate_id, sha=sha, status="pending-curation")],
    )


def test_ac4_curation_required(tmp_path):
    ledger = tmp_path / "l.ndjson"
    manifest = _pending_manifest()
    # no curation_approval ⇒ refused
    with pytest.raises(CurationRequiredError):
        admit_task(
            manifest, ledger, candidate_id="cand-1", task_sha="s" * 64, baseline_ref="b1"
        )
    assert manifest.is_schedulable("cand-1") is False


def test_ac4_baseline_prereq(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest()
    # approved but no clean baseline ⇒ refused
    record_curation_approval(
        ledger, ctx, candidate_id="cand-1", task_sha="s" * 64, approver="curator"
    )
    with pytest.raises(BaselinePrerequisiteError):
        admit_task(
            manifest, ledger, candidate_id="cand-1", task_sha="s" * 64, baseline_ref="b1"
        )

    # add a clean baseline for the sha ⇒ admitted + schedulable
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha="s" * 64, k=5,
        results=[{"run": i, "passed": True} for i in range(5)], verdict="clean",
    )
    task = admit_task(
        manifest, ledger, candidate_id="cand-1", task_sha="s" * 64, baseline_ref="b1"
    )
    assert task.status == "admitted"
    assert manifest.is_schedulable("cand-1") is True


def test_ac4_baseline_must_be_clean(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest()
    record_curation_approval(
        ledger, ctx, candidate_id="cand-1", task_sha="s" * 64, approver="curator"
    )
    # a quarantined baseline is not a clean baseline
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha="s" * 64, k=5,
        results=[{"run": 0, "passed": False}], verdict="quarantined",
    )
    with pytest.raises(BaselinePrerequisiteError):
        admit_task(
            manifest, ledger, candidate_id="cand-1", task_sha="s" * 64, baseline_ref="b1"
        )


# --- AC-5: boundary enforcement ---------------------------------------------
def test_ac5_boundary_enforced(tmp_path):
    from harness.corpus.registry import INSTRUMENT_ROOT

    # a path inside the instrument repo is structurally refused
    inside = CorpusManifest(
        corpus_id="internal-koala",
        semver="1.0.0",
        kind="internal",
        boundary_path=str(INSTRUMENT_ROOT / "corpora"),
        tasks=[],
    )
    with pytest.raises(BoundaryViolationError):
        inside.assert_boundary()

    # an internal corpus with no boundary is refused
    none = CorpusManifest(corpus_id="x", semver="1.0.0", kind="internal", tasks=[])
    with pytest.raises(BoundaryViolationError):
        none.assert_boundary()

    # a path outside the repo is fine
    ok = CorpusManifest(
        corpus_id="x", semver="1.0.0", kind="internal",
        boundary_path=str(tmp_path / "koala"), tasks=[],
    )
    ok.assert_boundary()  # does not raise


# --- AC-6: versioning + provenance ------------------------------------------
def test_ac6_mutation_requires_bump():
    v1 = CorpusManifest(
        corpus_id="c", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id="t", sha="a" * 64, status="admitted")],
    )
    # mutate content, keep semver ⇒ refused
    v1_mut = CorpusManifest(
        corpus_id="c", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id="t", sha="b" * 64, status="admitted")],
    )
    with pytest.raises(CorpusMutationError):
        v1_mut.assert_valid_successor(v1)

    # bump semver ⇒ allowed, and the bump re-triggers the baseline
    v1_1 = CorpusManifest(
        corpus_id="c", semver="1.0.1", kind="public",
        tasks=[TaskEntry(task_id="t", sha="b" * 64, status="admitted", baseline_ref="old")],
    )
    v1_1.assert_valid_successor(v1)  # does not raise
    v1_1.retrigger_baselines(v1)
    changed = v1_1.task("t")
    assert changed.baseline_ref is None
    assert changed.status == "pending-curation"


def test_ac6_semver_cited():
    manifest = CorpusManifest(
        corpus_id="terminal-bench", semver="2.1.0", kind="public",
        tasks=[
            TaskEntry(task_id="t2", sha="2" * 64, status="admitted"),
            TaskEntry(task_id="t1", sha="1" * 64, status="admitted"),
        ],
    )
    ref = manifest.provenance_ref()
    assert ref["corpus_id"] == "terminal-bench"
    assert ref["semver"] == "2.1.0"
    # task shas sorted by id — the byte-reconstructible citation
    assert ref["task_shas"] == {"t1": "1" * 64, "t2": "2" * 64}


def test_ac_non_harbor_format_rejected():
    # format is a Literal["harbor"] ⇒ a non-harbor task is unrepresentable [D003].
    with pytest.raises(ValidationError):
        TaskEntry(task_id="t", sha="a" * 64, format="custom")


def test_manifest_roundtrip(tmp_path):
    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    path = manifest.save(tmp_path / "m.json")
    loaded = CorpusManifest.load(path)
    assert loaded.to_json() == manifest.to_json()


def test_curation_approval_registered():
    assert "curation_approval" in events.REGISTERED_EVENTS
