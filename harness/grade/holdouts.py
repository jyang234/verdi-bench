"""First-class polymorphic holdouts [refactor 05 §1].

Before this module a "holdout" had no library representation: materialize wrote
arbitrary JSON, execution was delegated wholesale to out-of-repo grader images,
and every shakedown script hand-rolled assertion strings via ``python -c`` and
injected the results file by hand. This module gives the three shapes verdi
actually runs a typed, self-executing form — discriminated on ``kind`` and
serialized as the versioned ``holdout.json`` v1 contract [A2].

``holdout.json`` v1 (A2)::

    {"schema_version": 1, "kind": "assertion"|"pytest"|"command", "id": ..., ...}

A file **without** a ``kind`` stays exactly what it is today — opaque input for a
bespoke, benchmark-specific grader image (``corpus/materialize.py`` still writes
those). :func:`load_declared_holdout` returns ``None`` for such a file, so
nothing existing breaks; only declared kinds gain library execution.

Each kind implements:

- ``materialize(holdouts_dir)`` — write ``holdout.json`` (and any side files, e.g.
  the pytest file) into the read-only holdouts tree the grader mounts.
- ``execute(workspace) -> list[Assertion]`` — run the holdout against a trial
  workspace in a **subprocess** (``PYTHONDONTWRITEBYTECODE=1`` so importing the
  solution leaves no ``__pycache__`` in the graded diff — the divergence the two
  shakedown copies already grew; a timeout bound; the fence nonce scrubbed from
  the child's environment, deep-dive §2.4). The returned assertions carry the
  EXISTING ``holdout_test`` semantics, so ``deterministic.parse_holdout_output``
  is unchanged.

This module contains **no LLM client** — grading's determinism is its authority
(the ``grade-has-no-llm-clients`` import contract).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, TypeAdapter

from .container import NONCE_ENV
from .types import Assertion, AssertionResult

# The on-disk name of the declared-holdout spec. MUST match
# ``corpus/materialize.HOLDOUT_SPEC_FILENAME`` — both write/read this file.
HOLDOUT_SPEC_FILENAME = "holdout.json"

# The current holdout.json contract version [A2]. A transport/shape change bumps
# this (a versioned public seam — CLAUDE "public seams are contracts"): a file
# stamped with a version this build does not know fails validation loudly rather
# than being silently mis-read.
HOLDOUT_SCHEMA_VERSION = 1

# The source tag every holdout-test assertion carries — matches
# ``Assertion.is_holdout`` / ``deterministic.parse_holdout_output``. A holdout's
# result contributes to the binary score; a plugin's does not.
_HOLDOUT_SOURCE = "holdout_test"

# Safety bound on a single holdout's subprocess. Executing arbitrary agent-facing
# code needs a wall-clock ceiling so a hang cannot stall the batch; the docker
# path additionally wraps the whole grader in DockerGradeRunner's own timeout.
DEFAULT_TIMEOUT_S = 300


def assertions_to_raw(assertions: list[Assertion]) -> dict:
    """Pack executed assertions into the wire shape the grader parses.

    Both in-run consumers (:class:`~harness.grade.container.LocalExecutingGradeRunner`
    and the in-image ``run_holdouts`` entrypoint) emit
    ``{"assertions": [{"id", "result", "detail"?}]}`` so it flows through the
    FROZEN ``deterministic.parse_holdout_output`` (which re-stamps
    ``source="holdout_test"``) with zero change to that path.
    """
    out: list[dict] = []
    for a in assertions:
        item: dict = {"id": a.id, "result": a.result.value}
        if a.detail is not None:
            item["detail"] = a.detail
        out.append(item)
    return {"assertions": out}


def _fail_detail(proc: subprocess.CompletedProcess) -> str:
    """A bounded diagnostic for a failed holdout subprocess.

    Determinism note: on the docker path the workspace is always ``/workspace``
    and the holdouts ``/holdouts``, so any path in this text is container-fixed
    and the grade event is reproducible. On the ADVISORY
    ``LocalExecutingGradeRunner`` path the copy lives under a per-grade tmp dir,
    so a traceback embedding that path is not byte-stable across attempts —
    acceptable because that runner is ADVISORY (``grader_name != "docker"``) by
    construction [05 §1].
    """
    text = (proc.stderr or proc.stdout or "").strip()
    if not text:
        return f"holdout exited {proc.returncode}"
    return text[-800:]


class _HoldoutBase(BaseModel):
    """Common shape + execution machinery for the declared holdout kinds."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = HOLDOUT_SCHEMA_VERSION
    id: str = "h1"

    # Set by :func:`load_declared_holdout` (and :meth:`materialize`) so
    # :meth:`execute` can resolve side files (the pytest file) that live beside
    # ``holdout.json`` in the read-only holdouts dir. Not part of the contract.
    _source_dir: Optional[Path] = PrivateAttr(default=None)

    def materialize(self, holdouts_dir) -> None:
        """Write ``holdout.json`` v1 into ``holdouts_dir`` (subclasses may add
        side files). The dict is key-sorted + indented so the on-disk form is
        stable and diffable, mirroring ``corpus/materialize``'s writer."""
        holdouts_dir = Path(holdouts_dir)
        holdouts_dir.mkdir(parents=True, exist_ok=True)
        (holdouts_dir / HOLDOUT_SPEC_FILENAME).write_text(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self._source_dir = holdouts_dir

    def execute(self, workspace) -> list[Assertion]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _grade_argv(self, argv: list[str], workspace) -> list[Assertion]:
        """Run ``argv`` against ``workspace``; exit 0 = pass, else fail.

        The child runs with the fence nonce scrubbed from its environment
        (deep-dive §2.4: genuine protection is a subprocess whose exec-time env
        never held the nonce) and ``PYTHONDONTWRITEBYTECODE=1``.
        """
        env = os.environ.copy()
        env.pop(NONCE_ENV, None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.run(
                argv, cwd=str(workspace), capture_output=True, text=True,
                timeout=DEFAULT_TIMEOUT_S, env=env,
            )
        except subprocess.TimeoutExpired:
            return [Assertion(
                id=self.id, source=_HOLDOUT_SOURCE, result=AssertionResult.failed,
                detail=f"timed out after {DEFAULT_TIMEOUT_S}s",
            )]
        if proc.returncode == 0:
            return [Assertion(id=self.id, source=_HOLDOUT_SOURCE,
                              result=AssertionResult.passed)]
        return [Assertion(id=self.id, source=_HOLDOUT_SOURCE,
                          result=AssertionResult.failed, detail=_fail_detail(proc))]


class AssertionHoldout(_HoldoutBase):
    """kind="assertion": a ``python -c`` expression asserted against the workspace.

    Exactly the form the shakedown harness hand-rolls (``harbor.py`` ``HOLDOUTS``):
    the expression imports the agent's solution and asserts on it, so exit 0 (all
    asserts held) is a pass. Runs with ``cwd`` = the workspace, so ``from solution
    import ...`` resolves the agent's output [refactor 05 §1].
    """

    kind: Literal["assertion"] = "assertion"
    expression: str

    def execute(self, workspace) -> list[Assertion]:
        return self._grade_argv([sys.executable, "-c", self.expression], Path(workspace))


class PytestFileHoldout(_HoldoutBase):
    """kind="pytest": a pytest file (materialized beside ``holdout.json``) run
    against the workspace; exit 0 (all tests passed) is a pass [refactor 05 §1].

    ``body`` is a materialize-time-only input: it is written as the side file and
    is EXCLUDED from ``holdout.json`` (the side file is the content), so a spec
    reloaded from disk runs the materialized file and needs no body.
    """

    kind: Literal["pytest"] = "pytest"
    path: str = "test_holdout.py"
    body: Optional[str] = Field(default=None, exclude=True)

    def materialize(self, holdouts_dir) -> None:
        super().materialize(holdouts_dir)
        if self.body is not None:
            (Path(holdouts_dir) / self.path).write_text(self.body, encoding="utf-8")

    def execute(self, workspace) -> list[Assertion]:
        # The pytest file lives in the (read-only) holdouts dir, never the
        # workspace — resolve it from where the spec was loaded/materialized.
        base = self._source_dir if self._source_dir is not None else Path(workspace)
        test_file = base / self.path
        return self._grade_argv(
            [sys.executable, "-m", "pytest", "-q", str(test_file)], Path(workspace)
        )


class CommandHoldout(_HoldoutBase):
    """kind="command": an arbitrary argv run against the workspace; exit 0 = pass.

    The escape hatch for a benchmark whose check is a shell/binary rather than a
    Python assertion or a pytest file [refactor 05 §1].
    """

    kind: Literal["command"] = "command"
    argv: list[str]

    def execute(self, workspace) -> list[Assertion]:
        return self._grade_argv(list(self.argv), Path(workspace))


# The declared-holdout discriminated union — the closed set of library-executable
# kinds. A future kind adds a member here and bumps nothing else.
Holdout = Annotated[
    Union[AssertionHoldout, PytestFileHoldout, CommandHoldout],
    Field(discriminator="kind"),
]

_ADAPTER: TypeAdapter = TypeAdapter(Holdout)


def as_holdout(obj) -> _HoldoutBase:
    """Coerce a ``Holdout`` instance or a raw declaration dict into a validated
    ``Holdout`` (the SDK builder's front door for inline ``holdout=`` sugar)."""
    if isinstance(obj, _HoldoutBase):
        return obj
    return _ADAPTER.validate_python(obj)


def load_declared_holdout(holdouts_dir) -> Optional[_HoldoutBase]:
    """Load the declared ``Holdout`` from ``holdouts_dir/holdout.json``, or ``None``.

    Returns ``None`` when the file is absent OR carries no ``kind`` — an opaque,
    bespoke holdout spec (the pre-05 shape a benchmark-specific grader image
    consumes). Nothing about that path changes [A2]. A file WITH a ``kind`` that
    fails validation (unknown kind, bad version, extra keys) raises loudly rather
    than being silently skipped.
    """
    p = Path(holdouts_dir) / HOLDOUT_SPEC_FILENAME
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "kind" not in data:
        return None
    holdout = _ADAPTER.validate_python(data)
    holdout._source_dir = Path(holdouts_dir)
    return holdout
