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


def import_public_dataset(
    source: TaskSource,
    cache_dir,
    *,
    corpus_id: str,
    semver: str = "1.0.0",
    dataset_name: str = TERMINAL_BENCH,
    dataset_version: str = "2.0",
) -> CorpusManifest:
    """Import any public dataset ``source`` into ``cache_dir`` → its manifest.

    The generic engine behind :func:`import_terminal_bench` and the recognized
    benchmark importers in :mod:`harness.corpus.benchmarks` — a ``TaskSource``
    yields Harbor-format tasks and this routes them through the idempotent cache
    + manifest machinery regardless of which benchmark produced them.

    Idempotent for a fixed ``(source, dataset_version)``: tasks are keyed by id,
    shas compared, and unchanged content is written once. The task cache and the
    manifest are both deterministic byte-for-byte across re-imports. A record's
    ``metadata['created_at']`` (RFC 3339, when the source supplies it) rides onto
    the manifest entry so the contamination sentinel's cutoff dating has a real
    date rather than an honest ``unknown`` [EVAL-10 AC-1].
    """
    cache_dir = Path(cache_dir)
    tasks_dir = cache_dir / "tasks"

    # 1. Compute entries + intended cache writes first — no side effects yet, so
    #    a refused mutation (below) never rewrites the cache [CO-3].
    entries: list[TaskEntry] = []
    blobs: dict[str, str] = {}
    for raw in sorted(source.fetch(), key=lambda r: r.task_id):
        # One canonical serialization, reused for both the cache blob and its
        # sha — content_sha would re-serialize the same bytes.
        blob = json.dumps(
            raw.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        blobs[raw.task_id] = blob
        entries.append(
            TaskEntry(
                task_id=raw.task_id,
                sha=hashlib.sha256(blob.encode("utf-8")).hexdigest(),
                # Public dataset tasks are admitted as imported; internal tasks
                # go through the curation gate instead.
                status="admitted",
                metadata=raw.metadata,
                # A source-supplied creation date feeds cutoff dating; absent
                # stays None (honest `unknown`), never a wall-clock read.
                created_at=raw.metadata.get("created_at"),
            )
        )

    manifest = CorpusManifest(
        corpus_id=corpus_id,
        semver=semver,
        kind="public",
        dataset=Dataset(name=dataset_name, version=dataset_version),
        tasks=entries,
    )

    # 2. Enforce the successor rule and carry recorded state across a re-import
    #    against any prior manifest, BEFORE touching the cache [CO-3]. Same
    #    semver + changed content is refused; a clean re-import preserves
    #    calibration (previously wiped: full-run-validated -> none).
    prior_path = cache_dir / "manifest.json"
    if prior_path.exists():
        prior = CorpusManifest.load(prior_path)
        manifest.assert_valid_successor(prior)
        if manifest.semver == prior.semver:
            manifest.calibration = prior.calibration
            # PRA-M12: carry per-task recorded state for UNCHANGED tasks (same
            # sha), so a same-semver re-import is genuinely idempotent. Rebuilding
            # each entry as a fresh `admitted` silently reverted a quarantined
            # task to schedulable — and is_schedulable gates both the run
            # scheduler and the official fence. A changed sha is a new version of
            # the task and correctly keeps the fresh state.
            prior_by_id = {t.task_id: t for t in prior.tasks}
            for entry in manifest.tasks:
                pt = prior_by_id.get(entry.task_id)
                if pt is not None and pt.sha == entry.sha:
                    entry.status = pt.status
                    entry.baseline_ref = pt.baseline_ref
                    entry.canary_sha256 = pt.canary_sha256
                    if getattr(pt, "created_at", None) is not None:
                        entry.created_at = pt.created_at
        # A semver bump keeps calibration fresh: the new version must re-validate
        # before it can be cited officially.

    # 3. Now persist: write changed/absent blobs, then the manifest.
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task_id, blob in blobs.items():
        cache_path = tasks_dir / f"{task_id}.json"
        if not cache_path.exists() or cache_path.read_text(encoding="utf-8") != blob:
            cache_path.write_text(blob, encoding="utf-8")
    # CO-9: prune cache blobs for tasks that are no longer in the import, so the
    # cache does not drift from the manifest (a removed task's stale blob would
    # otherwise linger and could be re-read).
    current = {f"{task_id}.json" for task_id in blobs}
    for existing in tasks_dir.glob("*.json"):
        if existing.name not in current:
            existing.unlink()
    manifest.save(prior_path)
    return manifest


def import_terminal_bench(
    source: TaskSource,
    cache_dir,
    *,
    corpus_id: str = TERMINAL_BENCH,
    semver: str = "1.0.0",
    dataset_version: str = "2.0",
) -> CorpusManifest:
    """Import the terminal-bench public dataset — a thin, back-compatible alias
    for :func:`import_public_dataset` with the terminal-bench dataset name."""
    return import_public_dataset(
        source,
        cache_dir,
        corpus_id=corpus_id,
        semver=semver,
        dataset_name=TERMINAL_BENCH,
        dataset_version=dataset_version,
    )
