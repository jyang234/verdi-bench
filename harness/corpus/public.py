"""Public corpus import [EVAL-8 §M1, AC-1, D001].

``import_terminal_bench`` pulls a public dataset (terminal-bench@2.0) *through the
Harbor registry* into a local cache plus a :class:`CorpusManifest` recording the
dataset version and a content sha per task. The registry access is a **seam**
(:class:`TaskSource`) so the harness stays offline-testable and Harbor stays
confined to the run engine [import-linter contract]; the fixture source reads a
local directory.

Re-import against the same dataset version is **idempotent** [AC-1]: shas are
compared, unchanged tasks are neither duplicated nor churned, and the resulting
manifest is byte-identical to the prior one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .registry import CorpusManifest, Dataset, TaskEntry

TERMINAL_BENCH = "terminal-bench"


@dataclass(frozen=True)
class RawTask:
    """A task as pulled from the registry: id, harbor-format content, metadata."""

    task_id: str
    content: dict
    metadata: dict


class TaskSource(Protocol):
    """The registry seam. Real impls speak to Harbor; the fixture reads a dir."""

    def fetch(self) -> list[RawTask]: ...


def content_sha(content: dict) -> str:
    """Canonical sha256 over harbor task content — the citable task identity."""
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DirectorySource:
    """Fixture/local ``TaskSource``: each ``<task_id>.json`` is a harbor task.

    An optional sibling ``<task_id>.meta.json`` supplies stratification metadata.
    """

    def __init__(self, root):
        self.root = Path(root)

    def fetch(self) -> list[RawTask]:
        out: list[RawTask] = []
        for path in sorted(self.root.glob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            content = json.loads(path.read_text(encoding="utf-8"))
            meta_path = path.with_suffix(".meta.json")
            metadata = (
                json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists()
                else {}
            )
            out.append(RawTask(task_id=path.stem, content=content, metadata=metadata))
        return out


def import_terminal_bench(
    source: TaskSource,
    cache_dir,
    *,
    corpus_id: str = TERMINAL_BENCH,
    semver: str = "1.0.0",
    dataset_version: str = "2.0",
) -> CorpusManifest:
    """Import a public dataset into ``cache_dir`` and return its manifest.

    Idempotent for a fixed ``(source, dataset_version)``: tasks are keyed by id,
    shas compared, and unchanged content is written once. The task cache and the
    manifest are both deterministic byte-for-byte across re-imports.
    """
    cache_dir = Path(cache_dir)
    tasks_dir = cache_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    entries: list[TaskEntry] = []
    for raw in sorted(source.fetch(), key=lambda r: r.task_id):
        sha = content_sha(raw.content)
        cache_path = tasks_dir / f"{raw.task_id}.json"
        blob = json.dumps(
            raw.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        # Write only when absent or changed — no churn on a clean re-import.
        if not cache_path.exists() or cache_path.read_text(encoding="utf-8") != blob:
            cache_path.write_text(blob, encoding="utf-8")
        entries.append(
            TaskEntry(
                task_id=raw.task_id,
                sha=sha,
                # Public dataset tasks are admitted as imported; internal tasks
                # go through the curation gate instead.
                status="admitted",
                metadata=raw.metadata,
            )
        )

    manifest = CorpusManifest(
        corpus_id=corpus_id,
        semver=semver,
        kind="public",
        dataset=Dataset(name=TERMINAL_BENCH, version=dataset_version),
        tasks=entries,
    )
    manifest.save(cache_dir / "manifest.json")
    return manifest
