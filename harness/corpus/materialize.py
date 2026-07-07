"""Materialize an imported corpus into a runnable experiment [EVAL-8 → EVAL-4].

:func:`import_public_dataset` produces a cache of Harbor task content + a
manifest (the citable identity + admission state). This module is the bridge to
an *executable* experiment: it writes the ``tasks.yaml`` that ``bench run`` reads
and the per-task holdouts directory that ``bench grade`` mounts read-only — so
"plug in SWE-bench" is `import → materialize → plan → run`, not `import` then
hand-author every task by hand.

**The split is the point.** Each Harbor task carries an agent-visible portion
(``id`` / ``prompt`` / operational ``image``) and a grading ``holdout`` portion
(a benchmark's tests). Materialization routes the first into ``tasks.yaml`` (which
becomes the trial workspace's request) and the second into
``holdouts/<id>/holdout.json`` (mounted read-only at grade time, never in the
trial). The two never meet: they are read from different keys and written to
different files, so a benchmark's own tests cannot leak to the agent it is
grading [EVAL-4 AC-9]. A test asserts no holdout material appears in
``tasks.yaml``.

Materialization writes the grading *specification* (the tests to run). Executing
those tests is the grading image's job — for SWE-bench, an image that applies the
recorded ``test_patch`` and runs the recorded tests, emitting the
``holdout_results.json`` the deterministic grader parses. That image is the one
benchmark-specific, environment-bound piece verdi does not synthesize; the
holdout spec is the contract it consumes (documented in the usage guide).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from ..errors import VerdiRefusal
from .registry import CorpusManifest

# Keys of a Harbor task that are safe to show the agent (the trial request).
# Everything else — notably ``holdout`` — is grading material and is routed to
# the read-only holdouts dir instead. A closed allowlist, so a new content key
# is withheld by default rather than leaking to the agent by omission.
_AGENT_VISIBLE_KEYS = ("id", "prompt")

HOLDOUTS_SUBDIR = "holdouts"
HOLDOUT_SPEC_FILENAME = "holdout.json"


class MaterializeError(VerdiRefusal, RuntimeError):
    """The cache and manifest disagree — a task the manifest names has no cached
    content. Fail loud rather than silently materialize a partial corpus."""


def _cached_content(cache_dir: Path, task_id: str) -> dict:
    path = cache_dir / "tasks" / f"{task_id}.json"
    if not path.exists():
        raise MaterializeError(
            f"manifest names task {task_id!r} but its cache blob {path} is absent; "
            "re-run the import before materializing"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def materialize_experiment(
    manifest: CorpusManifest,
    cache_dir,
    dest_dir,
    *,
    only_admitted: bool = True,
) -> Path:
    """Write ``<dest>/tasks.yaml`` + ``<dest>/holdouts/<id>/holdout.json`` from a
    cached, imported corpus. Returns ``dest_dir``.

    ``only_admitted`` (default) materializes just the schedulable tasks — the run
    scheduler and official fence both gate on admission, so a pending/quarantined
    task in ``tasks.yaml`` would only be refused later; materializing it is
    misleading. Set it False to lay down every cached task.
    """
    cache_dir = Path(cache_dir)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    holdouts_root = dest_dir / HOLDOUTS_SUBDIR

    entries = [
        t for t in sorted(manifest.tasks, key=lambda t: t.task_id)
        if (t.status == "admitted") or not only_admitted
    ]

    tasks: list[dict] = []
    for entry in entries:
        content = _cached_content(cache_dir, entry.task_id)
        task: dict = {}
        for key in _AGENT_VISIBLE_KEYS:
            if key in content:
                task[key] = content[key]
        task.setdefault("id", entry.task_id)

        # operational wiring (image) rides the manifest metadata, not the citable
        # content — materialized into the runnable task, re-pinnable without
        # changing corpus identity.
        image = entry.metadata.get("image")
        if image:
            task["image"] = image

        # grading material → the read-only holdouts dir, never tasks.yaml.
        holdout = content.get("holdout")
        if holdout is not None:
            task_holdouts = holdouts_root / entry.task_id
            task_holdouts.mkdir(parents=True, exist_ok=True)
            (task_holdouts / HOLDOUT_SPEC_FILENAME).write_text(
                json.dumps(holdout, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            # a path RELATIVE to the experiment dir — the lock commits this path,
            # and grade mounts it read-only at /holdouts.
            task["holdouts_dir"] = f"{HOLDOUTS_SUBDIR}/{entry.task_id}"

        tasks.append(task)

    tasks_yaml = dest_dir / "tasks.yaml"
    tasks_yaml.write_text(
        yaml.safe_dump({"tasks": tasks}, sort_keys=False), encoding="utf-8"
    )
    return dest_dir


def agent_visible_leak(tasks_yaml_text: str, needles: list[str]) -> Optional[str]:
    """Return the first holdout ``needle`` that appears in a materialized
    ``tasks.yaml`` (the agent-visible surface), or None.

    A materialization-integrity check callers can assert on: a benchmark's test
    content must never be reachable from the task the agent sees.
    """
    for needle in needles:
        if needle and needle in tasks_yaml_text:
            return needle
    return None
