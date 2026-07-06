"""Grader plugin seam [EVAL-5 §M4].

A plugin is declared per task and contributes assertions to the vector; its
assertions feed fractional scoring but a plugin abstain must not fail the binary
score (that is computed over holdout-test assertions only). Plugins raising
anything become a ``cant_grade(plugin_error)`` upstream.

ISOLATION [PRA-M6]. On the real (docker) grade path, plugins run inside the SAME
fresh-copy, ``--network none`` container as holdout assertions (``--cap-drop ALL``,
no-new-privileges), launched via ``harness.grade.run_plugin`` in the grader
image — so a plugin that shells out over agent-authored build/config files has no
network and no host access. The no-daemon ``LocalGradeRunner`` runs plugins
in-process (an explicit ADVISORY fallback with no sandbox, for the fake/test path
only), and its grades are stamped ``grader_name="local"`` so they are
distinguishable from a trusted container grade. Only registry-declared plugins
resolve, so an arbitrary task can never inject code either way.
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


# The built-in plugin modules, imported below so importing THIS package
# registers them [refactor 01 §4 D3]. Registration must ride the package
# import itself: the in-container entrypoint (``harness.grade.run_plugin``)
# and the in-process ``LocalGradeRunner`` resolve plugins after importing only
# this package — a side-effect import parked in an unrelated module
# (grade/cli.py formerly held it) never runs inside the grader container,
# which made every real containerized plugin run an ``UnknownPluginError``.
BUILTIN_PLUGINS: tuple[str, ...] = ("groundwork",)


def _register_builtins() -> None:
    """Import every built-in plugin module so it self-registers.

    Runs at the bottom of the package body: the plugin modules import
    :class:`GraderPlugin` / :func:`register_plugin` from this package, so they
    are importable only once those names exist above."""
    from importlib import import_module

    for name in BUILTIN_PLUGINS:
        import_module(f".{name}", __name__)


_register_builtins()
