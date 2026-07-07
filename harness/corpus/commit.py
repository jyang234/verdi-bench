"""Task-content commitment [EVAL-8 §M1 / EVAL-1-D-6, PL-7, GR-5].

The set of tasks an experiment runs is pinned at lock time as a
``task_commitment`` inside the ``experiment_locked`` event; ``bench run`` and
``bench grade`` recompute it and refuse when the *task definitions* changed after
the lock. This is the Phase-1 slice of the corpus-manifest path: it binds the
``tasks.yaml`` task definitions (prompt, canaries, plugin ids, and the
``holdouts_dir`` **path**) to the immutable lock, closing the "swap tasks.yaml
after lock" hole.

**Coverage boundary (honest):** the commitment hashes each task's ``tasks.yaml``
entry, not the holdout *script files* that ``holdouts_dir`` points to. Swapping
the bytes of the holdout scripts on disk is therefore NOT detected here — that is
deferred to Phase 4, where holdouts live inside the versioned, content-hashed
corpus cache (``is_schedulable`` at run belongs to the same slice). Do not read
this module as protecting holdout-script contents.

Pure by construction: no ledger, network, or clock — only a canonical hash over
the task definitions, so plan/run/grade compute an identical commitment.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from ..errors import VerdiRefusal
from .public import content_sha


class TaskCommitmentError(VerdiRefusal, ValueError):
    """The tasks no longer match the commitment pinned at lock, or the task
    source is malformed [PL-7]."""


def task_content_sha(task: dict) -> str:
    """sha256 over a task's full canonical ``tasks.yaml`` definition — its
    committed identity.

    Covers every field of the entry (prompt, holdouts_dir path, canaries,
    plugins, …), so any post-lock change to the *definition* moves the sha. Not
    self-attested: a ``task_sha`` field in the source is ignored, it is
    recomputed. Does **not** cover the holdout script files under ``holdouts_dir``
    (see the module coverage boundary). Reuses the corpus canonical-hash
    primitive so the lock commitment and the manifest sha cannot drift.
    """
    return content_sha(task)


def holdout_content_sha(holdouts_dir) -> str:
    """sha256 over the on-disk holdout files the grader mounts under
    ``holdouts_dir`` — the bytes this module's commitment deliberately does NOT
    cover (see the coverage boundary above).

    Control-run reuse needs the *actual holdout script bytes* in its fingerprint
    (a stale control graded by a silently-changed holdout would be worthless), so
    this hashes what ``bench grade`` mounts read-only. Canonical over
    ``{relpath: sha256(bytes)}`` in sorted order, so identical trees on two
    machines hash identically; an absent or empty directory is a stable empty
    hash. Symlinks are skipped and reads are confined to the resolved tree — an
    agent/operator-planted link cannot pull foreign bytes into the fingerprint
    (the same no-follow stance the judge diff and grade container take).
    """
    root = Path(holdouts_dir)
    if not root.exists():
        return content_sha({})  # a task with no holdouts: the honest empty hash
    if not root.is_dir():
        # A holdouts_dir that resolves to a file or broken symlink is a
        # misconfiguration, not "no holdouts" — collapsing it to the empty hash
        # would let reuse pass across a silently mis-shaped holdout tree. Fail
        # loudly instead (the module's fail-loudly stance).
        raise TaskCommitmentError(
            f"holdouts_dir {root} exists but is not a directory; a mis-shaped "
            "holdout tree must fail loudly, not hash as empty"
        )
    root_real = root.resolve()
    files: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        if not p.resolve().is_relative_to(root_real):
            continue  # reached through a symlinked directory into another tree
        rel = p.relative_to(root).as_posix()
        files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return content_sha(files)


def load_task_dicts(experiment_dir) -> list[dict]:
    """Raw task dicts from ``<experiment_dir>/tasks.yaml`` (empty if absent).

    Validates the task source and sorts by id so the commitment is
    order-independent and this is the single reader plan/run/grade share. A task
    with no ``id`` or a duplicate ``id`` is refused loudly — a silently-collapsed
    duplicate would drop a task from the commitment and from ``which tasks ran``.
    """
    path = Path(experiment_dir) / "tasks.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tasks = data.get("tasks", [])
    seen: set = set()
    for t in tasks:
        if not isinstance(t, dict) or "id" not in t:
            raise TaskCommitmentError(f"tasks.yaml has a task with no 'id': {t!r}")
        tid = t["id"]
        if tid in seen:
            raise TaskCommitmentError(
                f"tasks.yaml has duplicate task id {tid!r}; task ids must be unique"
            )
        seen.add(tid)
    return sorted(tasks, key=lambda t: t["id"])


def compute_commitment(task_dicts, *, corpus_id: str, semver: str) -> dict:
    """The pinned commitment: corpus id/semver + one hash over the per-task shas.

    ``corpus_id``/``semver`` are recorded for a self-describing, auditable event
    (which corpus the experiment committed to); the ``task_shas_sha256`` is the
    hash that actually binds the task *content*.
    """
    shas = {t["id"]: task_content_sha(t) for t in task_dicts}
    return {
        "corpus_id": corpus_id,
        "semver": semver,
        "task_shas_sha256": content_sha(shas),
    }


def assert_task_commitment(lock_event: dict, task_dicts, *, corpus_id: str, semver: str) -> None:
    """Fail closed unless the tasks match the lock's ``task_commitment`` [PL-7].

    A missing commitment is itself a refusal: an experiment that runs real tasks
    must have committed to them at plan time.
    """
    committed = lock_event.get("task_commitment")
    if committed is None:
        raise TaskCommitmentError(
            "experiment_locked carries no task_commitment: the tasks were not "
            "committed at plan time. Re-plan with tasks.yaml present."
        )
    recomputed = compute_commitment(task_dicts, corpus_id=corpus_id, semver=semver)
    if recomputed != committed:
        raise TaskCommitmentError(
            "tasks.yaml no longer matches the commitment pinned at lock — a task "
            "definition (prompt / canaries / plugins / holdouts_dir) changed after "
            "lock. Refusing. (Holdout script file contents are not covered by this "
            "commitment; see corpus.commit's coverage boundary.)"
        )
