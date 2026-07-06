"""``TaskSpec`` (write side) + ``tasks_to_yaml`` [refactor 02 §2, decision A9].

The model types the authoring side and refuses unknown keys; the lenient reader
(``load_task_dicts``) must still read what the writer emits, byte-for-byte
unchanged. These tests pin the write side and prove the read side is untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.corpus.commit import load_task_dicts, task_content_sha
from harness.schema.tasks import TaskSpec, tasks_to_yaml

REPO = Path(__file__).resolve().parents[1]


def test_taskspec_accepts_full_field_vocabulary():
    """Every field a harness consumer reads is representable."""
    t = TaskSpec(
        id="t1",
        prompt="Write solution.py defining add(a, b).",
        image="verdi-bench/agent@sha256:" + "a" * 64,
        timeout_s=900,
        holdouts_dir="holdouts/t1",
        plugin_ids=["ruff-clean"],
        task_class="refactor",
        holdout_canaries=["CANARY-XYZ"],
        fake_behavior={"native_log": {"total_cost_usd": 0.02}},
    )
    assert t.id == "t1"
    assert t.plugin_ids == ["ruff-clean"]


def test_taskspec_forbids_unknown_keys():
    """extra='forbid' on the write side — an unrecognized key is a rejection."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskSpec(id="t1", holdout_dir="holdouts/t1")  # the classic typo (drift trap)


def test_tasks_to_yaml_is_minimal_and_reloads_via_lenient_reader(tmp_path):
    """The emitted file omits unset optionals and is read back unchanged by the
    lenient loader that feeds the lock hash (byte-untouched read side, A9)."""
    tasks = [
        TaskSpec(id="t2", prompt="second"),
        TaskSpec(id="t1", prompt="first", holdouts_dir="holdouts/t1",
                 plugin_ids=["p"], task_class="bugfix"),
    ]
    (tmp_path / "tasks.yaml").write_text(tasks_to_yaml(tasks), encoding="utf-8")

    # minimal: the bare task carries only the keys it set
    raw = yaml.safe_load((tmp_path / "tasks.yaml").read_text())["tasks"]
    by_id = {t["id"]: t for t in raw}
    assert set(by_id["t2"]) == {"id", "prompt"}  # defaults omitted
    assert "fake_behavior" not in by_id["t2"]
    assert set(by_id["t1"]) == {"id", "prompt", "holdouts_dir", "plugin_ids", "task_class"}

    # the lenient reader loads it (and sorts by id, as it does for the commitment)
    dicts = load_task_dicts(tmp_path)
    assert [d["id"] for d in dicts] == ["t1", "t2"]
    # re-parsing each emitted entry through the strict model yields an equal spec
    reloaded = {d["id"]: TaskSpec(**d) for d in dicts}
    assert reloaded["t1"] == tasks[1]
    assert reloaded["t2"] == tasks[0]


def test_tasks_to_yaml_refuses_duplicate_ids():
    """Emitting a file the reader would reject is a write-side defect — refuse."""
    with pytest.raises(ValueError, match="duplicate task id"):
        tasks_to_yaml([TaskSpec(id="dup"), TaskSpec(id="dup")])


def test_emitted_tasks_are_hashable_by_the_reader(tmp_path):
    """task_content_sha (the lock-feeding recipe) runs unchanged on emitted
    entries — the write side produces exactly what the commitment consumes."""
    (tmp_path / "tasks.yaml").write_text(
        tasks_to_yaml([TaskSpec(id="t1", prompt="p", task_class="feature")]),
        encoding="utf-8",
    )
    [d] = load_task_dicts(tmp_path)
    # deterministic and stable
    assert task_content_sha(d) == task_content_sha(d)


def test_real_shakedown_golden_parses_strictly():
    """Vocabulary completeness: a real tasks.yaml (the shakedown golden) parses
    through the strict model with no unknown-key rejection — proof the field set
    matches what real files carry."""
    golden = REPO / "scripts" / "shakedown" / "assets" / "golden" / "tasks.yaml"
    entries = yaml.safe_load(golden.read_text(encoding="utf-8"))["tasks"]
    specs = [TaskSpec(**e) for e in entries]  # would raise on any unknown key
    assert {s.id for s in specs} == {f"t{i}" for i in range(1, 9)}
    assert any(s.fake_behavior for s in specs)
    assert all(s.task_class for s in specs)
