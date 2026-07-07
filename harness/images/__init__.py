"""``harness.images`` — build, pin, and verify trial images [refactor 03 §4].

The product goal: a test image should be trivial to create and TRUST. This
subsystem builds a plain docker context, pins it to a sha256, and proves it obeys
the harbor compatibility contract (``docs/images.md`` §1) via an offline
``verify`` that reads no engine source — it CONSUMES the run engine's docker layer
(:mod:`harness.hermetic`), never harbor itself, and re-implements no trial
execution [refactor 03 §7]. It also owns :class:`EnvironmentSpec`, the canonical
statement of a task's declared environment (A3).
"""

from __future__ import annotations

from harness.images.build import (
    ImageBuildError,
    ImageResolveError,
    build,
    resolve_digest,
)
from harness.images.registry import (
    UnknownImageError,
    official_images,
    official_names,
    resolve,
)
from harness.images.spec import (
    ENVIRONMENT_FIELDS,
    ComplianceCheck,
    ComplianceReport,
    EnvironmentSpec,
    ImageSpec,
    PinnedImage,
)
from harness.images.verify import verify

__all__ = [
    "ImageSpec",
    "EnvironmentSpec",
    "ENVIRONMENT_FIELDS",
    "PinnedImage",
    "ComplianceCheck",
    "ComplianceReport",
    "build",
    "resolve_digest",
    "verify",
    "resolve",
    "official_images",
    "official_names",
    "UnknownImageError",
    "ImageBuildError",
    "ImageResolveError",
]
