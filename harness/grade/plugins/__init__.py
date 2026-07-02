"""Grader plugin seam [EVAL-5 §M4].

A plugin is declared per task and contributes assertions to the vector; its
assertions feed fractional scoring but a plugin abstain must not fail the binary
score (that is computed over holdout-test assertions only). Plugins raising
anything become a ``cant_grade(plugin_error)`` upstream.
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
