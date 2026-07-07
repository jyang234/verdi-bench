"""The official image registry [refactor 03 §4].

Maps an official image NAME to its build context under ``images/`` and drives
``bench images list``. Also owns the base-image constants the builder consults to
satisfy a ``FROM verdi-base`` dependency. Pure data + path resolution — no Docker,
no build (that is :mod:`harness.images.build`), so there is no import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..errors import VerdiRefusal
from .spec import ImageSpec

# ``images/`` lives at the repo root: harness/images/registry.py → parents[2].
IMAGES_ROOT = Path(__file__).resolve().parents[2] / "images"
BASE_CONTEXT = IMAGES_ROOT / "base"
BASE_TAG = "verdi-base"


@dataclass(frozen=True)
class OfficialImage:
    """One registry entry: an official image's context, tag, and declared format."""

    name: str
    context: Path
    tag: str
    expected_format: str = "generic"
    platform: Optional[str] = None
    description: str = ""

    def to_spec(self) -> ImageSpec:
        return ImageSpec(
            ref=self.tag,
            build_context=self.context,
            expected_format=self.expected_format,
            platform=self.platform,
        )


# The maintained official images. base is listed so `bench images build base` and
# `bench images list` see it; the stack images emit the GENERIC format via
# verdi_agent (their agent.py invokes the pinned CLI and translates), so their
# declared format is generic — native claude_code/codex emission is the documented
# alternative, not what these agents do [refactor 03 §3].
_OFFICIAL: dict[str, OfficialImage] = {
    img.name: img
    for img in (
        OfficialImage(
            "base",
            BASE_CONTEXT,
            BASE_TAG,
            description="verdi-base: the maintained base every trial image extends",
        ),
        OfficialImage(
            "generic-llm",
            IMAGES_ROOT / "official" / "generic-llm",
            "verdi-bench/generic-llm:latest",
            description="single-turn chat agent (anthropic/openai/google), platform: generic",
        ),
        OfficialImage(
            "anthropic-claude-code",
            IMAGES_ROOT / "official" / "anthropic-claude-code",
            "verdi-bench/anthropic-claude-code:latest",
            description="drives the pinned Claude Code CLI; emits generic via verdi_agent",
        ),
        OfficialImage(
            "openai-codex",
            IMAGES_ROOT / "official" / "openai-codex",
            "verdi-bench/openai-codex:latest",
            description="drives the pinned OpenAI Codex CLI; emits generic via verdi_agent",
        ),
    )
}


def official_names() -> list[str]:
    """The official image names, in registry order [drives ``bench images list``]."""
    return list(_OFFICIAL)


def official_images() -> list[OfficialImage]:
    return list(_OFFICIAL.values())


def get_official(name: str) -> Optional[OfficialImage]:
    return _OFFICIAL.get(name)


class UnknownImageError(VerdiRefusal, ValueError):
    """A ``bench images build`` target is neither an official name nor a context dir."""


def resolve(name_or_path: str) -> ImageSpec:
    """Resolve a build target to an :class:`ImageSpec` [refactor 03 §4].

    An official NAME resolves through the registry; anything else is treated as a
    build-context PATH (a plain docker context, e.g. the moved multi-agent
    reference), tagged from its directory name. A target that is neither a known
    name nor an existing directory is refused loudly.
    """
    official = _OFFICIAL.get(name_or_path)
    if official is not None:
        return official.to_spec()
    path = Path(name_or_path)
    if path.is_dir() and (path / "Dockerfile").exists():
        return ImageSpec(
            ref=f"verdi-local/{path.resolve().name}:latest",
            build_context=path,
        )
    raise UnknownImageError(
        f"{name_or_path!r} is not an official image ({official_names()}) and is not "
        "a build context directory with a Dockerfile"
    )
