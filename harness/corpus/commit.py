"""Task-content commitment [EVAL-8 §M1 / EVAL-1-D-6, PL-7, GR-5].

The set of tasks an experiment runs is pinned at lock time as a
``task_commitment`` inside the ``experiment_locked`` event; ``bench run`` and
``bench grade`` recompute it and refuse when the task content changed after the
lock. This is the Phase-1 slice of the corpus-manifest path: it binds *which
task content* an experiment executed to the immutable lock, closing the
"swap ``tasks.yaml`` (prompts, canaries, holdout scripts, scripted grades) after
lock" hole. Full manifest+cache-as-source (holdout import into the cache,
``is_schedulable`` at run) is the deferred remainder.

Pure by construction: no ledger, network, or clock — only a canonical hash over
the task definitions, so plan/run/grade compute an identical commitment.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


class TaskCommitmentError(ValueError):
    """The tasks no longer match the commitment pinned at lock [PL-7]."""


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def task_content_sha(task: dict) -> str:
    """sha256 over a task's full canonical definition — its committed identity.

    Covers every field of the ``tasks.yaml`` entry (prompt, holdouts_dir,
    canaries, plugins, …), so any post-lock content change moves the sha. Not
    self-attested: a ``task_sha`` field in the source is ignored, it is recomputed.
    """
    return hashlib.sha256(_canon(task).encode("utf-8")).hexdigest()


def load_task_dicts(experiment_dir) -> list[dict]:
    """Raw task dicts from ``<experiment_dir>/tasks.yaml`` (empty if absent).

    Sorted by id so the commitment is order-independent. This is the single
    reader of the task source that plan, run, and grade all share.
    """
    path = Path(experiment_dir) / "tasks.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return sorted(data.get("tasks", []), key=lambda t: t["id"])


def compute_commitment(task_dicts, *, corpus_id: str, semver: str) -> dict:
    """The pinned commitment: corpus id/semver + one hash over the per-task shas."""
    shas = {t["id"]: task_content_sha(t) for t in task_dicts}
    return {
        "corpus_id": corpus_id,
        "semver": semver,
        "task_shas_sha256": hashlib.sha256(_canon(shas).encode("utf-8")).hexdigest(),
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
            "tasks.yaml no longer matches the commitment pinned at lock — task "
            "content (prompts / canaries / holdout scripts / scoring) changed "
            "after lock. Refusing."
        )
