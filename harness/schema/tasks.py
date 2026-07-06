"""``tasks.yaml`` task entries — the WRITE-SIDE model [refactor 02 §2, A9].

``tasks.yaml`` had no model at all: ``harness/corpus/commit.py`` validated only
id-uniqueness and hashed each entry's raw bytes into the lock. :class:`TaskSpec`
types the *authoring* side so the SDK builder and the ``validate-tasks`` lint
verb reject unknown keys and the known drift traps — while the read side stays
byte-for-byte lenient.

**Read side is deliberately NOT this model (decision A9).** ``load_task_dicts``
(``harness/corpus/commit.py``) feeds the task-content sha that rides the lock;
routing it through a strict model would change which bytes hash and could brick
an already-locked experiment. So this model is used only to *emit* and to *lint*
new files pre-lock — ``extra="forbid"`` here, lenient ``.get(...)`` there.

Field vocabulary is the union of every ``tasks.yaml`` key the harness actually
reads (each field's docstring names its consumer). New fields must stay
optional-with-default (compatibility stance, 02 §7): ``extra="forbid"`` makes
version skew one-directional — a newer file fails an older reader, never the
reverse.
"""

from __future__ import annotations

from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


class TaskSpec(BaseModel):
    """One ``tasks.yaml`` entry, write-side. Read side stays lenient (A9)."""

    model_config = ConfigDict(extra="forbid")

    # Required identity — the key every consumer joins on (corpus/commit.py,
    # grade/cli.py, run/cli.py, judge/cli.py, review/build.py, forensics/scan.py,
    # contamination/cli.py, status/aggregate.py, analyze/cli.py, author/server.py,
    # run/control_reuse.py). Uniqueness across a file is enforced by the reader
    # and by :func:`tasks_to_yaml`.
    id: str

    # Agent-visible task text [run/cli.py, judge/cli.py, review/build.py,
    # contamination/cli.py]. Optional: telemetry/judge-only tasks may omit it,
    # matching the lenient reader's ``t.get("prompt", "")``.
    prompt: str = ""

    # Pinned image ref/digest for the trial container [run/cli.py; written by
    # corpus/materialize.py]. Absent ⇒ the run seam applies its fake-agent
    # default (``run/types.py`` Task.image), so it is omitted when unset.
    image: Optional[str] = None

    # Per-task wall-clock timeout override in seconds [run/cli.py]. Absent ⇒ the
    # engine default (DEFAULT_TIMEOUT_S).
    timeout_s: Optional[int] = None

    # Read-only holdout tree the grader mounts [grade/cli.py, forensics/scan.py,
    # contamination/cli.py, run/control_reuse.py; written by
    # corpus/materialize.py]. A path relative to the experiment dir; the lock
    # commits the PATH, not the script bytes (commit.py coverage boundary).
    holdouts_dir: Optional[str] = None

    # Custom grader plugin ids run in-container [grade/cli.py, run/reuse.py].
    plugin_ids: list[str] = Field(default_factory=list)

    # Calibration/stratification label [judge/cli.py, review/build.py; documented
    # in docs/usage-guide.md §2.3]. Absent ⇒ readers apply "default".
    task_class: Optional[str] = None

    # Canary strings seeded into holdouts — must never reach the trial [run/cli.py,
    # AC-9]. Carried on the task so the run seam can plant them.
    holdout_canaries: list[str] = Field(default_factory=list)

    # FAKE-ENGINE ONLY deterministic scripting for tests/shakedown [run/cli.py].
    # Never read by the grader or a real engine; named here at last (32 test
    # files use it undocumented, per 02 §2).
    fake_behavior: dict = Field(default_factory=dict)

    # --- refactor 05 §1 / A3: inline holdout sugar (BUILDER-INPUT-ONLY) ----------
    # An inline declared holdout — a ``grade.holdouts.Holdout`` object or a raw
    # declaration dict. The write path (SDK ``Experiment.write`` /
    # ``compile_inline_holdout``) compiles it OUT: it materializes
    # ``holdouts/<id>/`` and sets ``holdouts_dir``, so the emitted tasks.yaml keeps
    # exactly today's on-disk shape (holdouts_dir only). ``exclude=True`` ENFORCES
    # that it is never serialized. Typed loosely so schema/ takes no grade/ import;
    # the builder validates it through the Holdout hierarchy.
    holdout: Optional[Any] = Field(default=None, exclude=True)
    # --- EnvironmentSpec fields [refactor 03 §5, A3] ------------------------
    # A task's declared environment. Additive, optional, sha-covered by the task
    # commitment (raw bytes). The canonical model is
    # harness.images.spec.EnvironmentSpec (a schema→images import would invert the
    # dependency direction, so the three fields are carried flat here; a parity
    # test keeps the sets identical). Consumed by run/api.py: `files` + `env` reach
    # the engine (staged into /workspace / injected as non-secret env); `extra_hosts`
    # extends the derived proxy allowlist (harness/run/egress.py).
    files: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    extra_hosts: list[str] = Field(default_factory=list)


def tasks_to_yaml(tasks: list[TaskSpec]) -> str:
    """Serialize task specs to ``tasks.yaml`` text: ``{"tasks": [ ... ]}``.

    Emits each entry via ``model_dump(mode="json", exclude_defaults=True)`` so an
    unset optional is omitted rather than written as an empty/null placeholder —
    the minimal file the lenient reader (``load_task_dicts``) re-reads unchanged.
    Refuses duplicate ids loudly: the reader rejects them, so emitting a file it
    cannot load would be a silent write-side defect (fail-loudly directive).

    Pre-lock only, like :func:`harness.schema.serialize.spec_to_yaml`: the task
    content shas ride the lock, so nothing may rewrite a locked ``tasks.yaml``.
    """
    seen: set[str] = set()
    entries = []
    for t in tasks:
        if t.id in seen:
            raise ValueError(
                f"duplicate task id {t.id!r}; task ids must be unique within a "
                "tasks.yaml (the reader refuses duplicates)"
            )
        seen.add(t.id)
        entries.append(t.model_dump(mode="json", exclude_defaults=True))
    return yaml.safe_dump({"tasks": entries}, sort_keys=False, allow_unicode=True)
