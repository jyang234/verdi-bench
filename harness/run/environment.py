"""Materialize a task's declared environment into the workspace [refactor 03 §5, A3].

A task may declare ``files`` (``{relative/path: contents}``) that the engine stages
into ``/workspace`` *before* the trial starts — a fixture tree, a scaffold, a
starter file the agent works from. Both the Harbor and the fake engine call
:func:`stage_files`, so L1 hermetic tests stay meaningful (the fake materializes
the same files a real container would see) [refactor 03 §5].

Staging is escape-confined: a path that is absolute or climbs out of the workspace
(``../etc/...``) is refused loudly — a declared fixture must never write outside the
graded workspace, the same confinement the workspace hash walk enforces [PRA-M5].
"""

from __future__ import annotations

from pathlib import Path


class EnvironmentStagingError(RuntimeError):
    """A declared ``files`` entry could not be staged safely [refactor 03 §5]."""


def stage_files(workspace, files: dict[str, str]) -> None:
    """Write each ``rel: contents`` of ``files`` under ``workspace`` [A3].

    A no-op for an empty/absent declaration (the common case). Refuses an absolute
    path or one that escapes the workspace rather than staging outside the graded
    tree.
    """
    if not files:
        return
    ws = Path(workspace).resolve()
    for rel, contents in files.items():
        candidate = Path(rel)
        if candidate.is_absolute():
            raise EnvironmentStagingError(
                f"declared file {rel!r} is an absolute path; environment files must "
                "be relative to /workspace"
            )
        target = (ws / candidate).resolve()
        if not target.is_relative_to(ws):
            raise EnvironmentStagingError(
                f"declared file {rel!r} escapes the workspace ({target}); refusing to "
                "stage outside the graded tree"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
