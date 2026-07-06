"""Starter templates — the single source of the example spec + rubric [refactor 02 §2].

The audit found the "example spec" copy-pasted into ≥5 places (docs, test
builders, the author page template, entrypoint fixture blocks, the shakedown
scripts) — several still naming a retired model id. These data files are the ONE
canonical copy; every other surface derives from them:

- ``starter-experiment.yaml`` — consumed by the author page's template pane
  (``harness/author/page.py``), the ``bench init`` scaffold, the usage-guide
  example (pinned by a docs-consistency test), and the test fixture builders.
- ``judge-rubric.md`` — the library rubric the ``Experiment.judge(rubric=None)``
  default writes; it carries the verdict-JSON contract block (winner / reason /
  evidence-locator / confidence) that was hand-embedded in the shakedown rubrics
  (decision A8: the verdict-JSON format stays in rubrics, and this template is
  its single source).

The loaders below read the raw bytes so callers get *exactly* the committed file
(no re-serialization). Surfaces forbidden from importing ``harness.sdk`` by the
sdk-is-a-leaf contract (e.g. ``harness.author``, ``bench init`` in
``harness.cli``) read these files directly via :data:`TEMPLATES_DIR` instead of
calling these functions — the file is the shared contract, not the code.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent

STARTER_EXPERIMENT = TEMPLATES_DIR / "starter-experiment.yaml"
STARTER_TASKS = TEMPLATES_DIR / "starter-tasks.yaml"
JUDGE_RUBRIC = TEMPLATES_DIR / "judge-rubric.md"


def starter_spec_text() -> str:
    """The canonical starter ``experiment.yaml`` text (raw committed bytes)."""
    return STARTER_EXPERIMENT.read_text(encoding="utf-8")


def starter_tasks_text() -> str:
    """The canonical starter ``tasks.yaml`` text (raw committed bytes)."""
    return STARTER_TASKS.read_text(encoding="utf-8")


def judge_rubric_text() -> str:
    """The library judge rubric — the ``judge(rubric=None)`` default and the
    single source of the verdict-JSON output contract [decision A8]."""
    return JUDGE_RUBRIC.read_text(encoding="utf-8")


__all__ = [
    "TEMPLATES_DIR",
    "STARTER_EXPERIMENT",
    "STARTER_TASKS",
    "JUDGE_RUBRIC",
    "starter_spec_text",
    "starter_tasks_text",
    "judge_rubric_text",
]
