"""``build_manifest`` centralizes the hand-rolled manifest envelope [refactor 02 §1].

Pins the ``{corpus_id, semver, kind, tasks:[{task_id, sha, status, metadata}]}``
shape and proves it reproduces the three former hand-writers' output exactly —
their consumers pin behavior, so the centralization must be byte-equivalent.
"""

from __future__ import annotations

import hashlib

import pytest

from harness.corpus.manifest import build_manifest
from harness.corpus.registry import CorpusManifest, TaskEntry


def test_shape_and_admitted_default():
    m = build_manifest(
        corpus_id="c", semver="1.0.0", kind="public",
        tasks=[{"task_id": "t0", "sha": "a" * 64}],
    )
    assert isinstance(m, CorpusManifest)
    assert (m.corpus_id, m.semver, m.kind) == ("c", "1.0.0", "public")
    [entry] = m.tasks
    assert entry.task_id == "t0" and entry.sha == "a" * 64
    assert entry.status == "admitted"  # builder default (not TaskEntry's default)
    assert entry.metadata == {}


def test_status_and_metadata_pass_through():
    m = build_manifest(
        corpus_id="c", semver="2.3.4", kind="public",
        tasks=[
            {"task_id": "t0", "sha": "0" * 64, "status": "pending-curation"},
            {"task_id": "t1", "sha": "1" * 64, "metadata": {"category": "bugfix"}},
        ],
    )
    by_id = {t.task_id: t for t in m.tasks}
    assert by_id["t0"].status == "pending-curation"  # explicit override honored
    assert by_id["t1"].metadata == {"category": "bugfix"}
    assert by_id["t1"].status == "admitted"


def test_unknown_task_key_is_refused():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        build_manifest(corpus_id="c", semver="1.0.0",
                       tasks=[{"task_id": "t0", "sha": "a" * 64, "bogus": 1}])


def test_bad_semver_refused_by_model():
    # the CorpusManifest validator (CorpusError) fires inside construction; pydantic
    # surfaces it as a ValidationError — both are ValueError, and the point is that
    # build_manifest validates rather than emitting a malformed manifest silently.
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        build_manifest(corpus_id="c", semver="1.0", tasks=[])


def test_reproduces_shakedown_hand_writer_shape():
    """tripwires.py / official.py projection: task_id=id, sha=sha256(id),
    status=admitted, metadata={category: task_class or misc}."""
    tasks = [{"id": "t1", "task_class": "refactor"}, {"id": "t2"}]
    built = build_manifest(
        corpus_id="shakedown-mini", semver="1.0.0", kind="public",
        tasks=[
            {"task_id": t["id"],
             "sha": hashlib.sha256(t["id"].encode()).hexdigest(),
             "status": "admitted",
             "metadata": {"category": t.get("task_class", "misc")}}
            for t in tasks
        ],
    )
    # exactly the dict the scripts hand-write today (order-independent per task)
    assert built.model_dump(mode="json")["tasks"] == [
        {"task_id": "t1", "sha": hashlib.sha256(b"t1").hexdigest(), "format": "harbor",
         "status": "admitted", "baseline_ref": None, "plugins": [],
         "metadata": {"category": "refactor"}, "miner": None, "created_at": None,
         "canary_sha256": None},
        {"task_id": "t2", "sha": hashlib.sha256(b"t2").hexdigest(), "format": "harbor",
         "status": "admitted", "baseline_ref": None, "plugins": [],
         "metadata": {"category": "misc"}, "miner": None, "created_at": None,
         "canary_sha256": None},
    ]


def test_full_corpus_fixture_rebase_is_equivalent():
    """The scenarios.full_corpus rebase must be byte-identical to the old
    hand-built CorpusManifest (its analyze/findings consumers pin behavior)."""
    old = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id=f"task{i}", sha="a" * 64, status="admitted")
               for i in range(5)],
    )
    old.calibration.status = "full-run-validated"

    from tests.fixtures.scenarios import full_corpus

    new = full_corpus()
    assert new.to_json() == old.to_json()
