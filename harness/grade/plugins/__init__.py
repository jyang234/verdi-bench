"""Grader plugin seam [EVAL-5 §M4].

A plugin is declared per task and contributes assertions to the vector; its
assertions feed fractional scoring but a plugin abstain must not fail the binary
score (that is computed over holdout-test assertions only). Plugins raising
anything become a ``cant_grade(plugin_error)`` upstream.

ISOLATION ASYMMETRY — read before writing a plugin [PRA-M6]. Holdout assertions
run inside the fresh-copy, ``--network none`` grading container. **Grader plugins
currently run IN-PROCESS in the harness host**, with full network and filesystem
access, against the agent-controlled workspace — they are NOT sandboxed. Only
registry-declared plugins resolve (an arbitrary task cannot inject code), but a
registered plugin that shells out over agent-authored build/config files is a
host-side code-execution surface the holdout tier does not have. Treat plugins as
**trusted operator code**, not as part of the network-less guarantee. Running
plugins under the same container discipline as holdouts is the intended
hardening (REVIEW-D-P8-2, containerize-plugins) and requires a grading image
that hosts the plugin toolchain; until that lands, this asymmetry is a
documented, deliberate limitation, not an oversight.
"""

from __future__ import annotations

from typing import Type

from ..types import Assertion, AssertionResult, GradeTask


class GraderPlugin:
    """Contract: ``(workspace, task) -> [Assertion]``."""

    id: str = "base"

    def grade(self, workspace, task: GradeTask) -> list[Assertion]:  # pragma: no cover
        raise NotImplementedError


_REGISTRY: dict[str, Type[GraderPlugin]] = {}


class UnknownPluginError(KeyError):
    pass


def register_plugin(cls: Type[GraderPlugin]) -> Type[GraderPlugin]:
    _REGISTRY[cls.id] = cls
    return cls


def get_plugin(plugin_id: str) -> GraderPlugin:
    try:
        return _REGISTRY[plugin_id]()
    except KeyError:
        raise UnknownPluginError(
            f"no grader plugin {plugin_id!r}; known: {sorted(_REGISTRY)}"
        ) from None
