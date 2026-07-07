"""Image specs and compliance types [refactor 03 §4].

The typed vocabulary of the images subsystem: what to build/pin (:class:`ImageSpec`),
what a build produced (:class:`PinnedImage`), what ``verify`` concluded
(:class:`ComplianceReport`), and the per-task environment declaration
(:class:`EnvironmentSpec`, A3). All are ``extra="forbid"`` — an unknown key is a
typo, not a silent no-op.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class ImageSpec(BaseModel):
    """What to build or resolve, and how ``verify`` should read its log [refactor 03 §4].

    ``build_context`` ``None`` means "pull / resolve a local ref only" (no build).
    ``expected_format`` selects the parser ``verify`` validates the emitted
    ``agent_log.json`` against: ``generic`` (the verdi normalized format) or
    ``native``, in which case ``platform`` names the registered adapter
    (``claude_code`` / ``codex``) whose native parser applies.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    build_context: Optional[Path] = None
    expected_format: Literal["generic", "native"] = "generic"
    platform: Optional[str] = None


class EnvironmentSpec(BaseModel):
    """A task's declared environment [refactor 03 §5, A3] — additive, optional.

    These are ordinary ``tasks.yaml`` fields: sha-covered by the task commitment
    like everything else, written pre-lock only, materialized by the engine
    *before* the trial starts. The write-side :class:`~harness.schema.tasks.TaskSpec`
    and the SDK ``Task`` carry the same three fields flat (a schema→images import
    would be the wrong dependency direction); a parity test keeps the field sets
    identical, so this is the single canonical statement of the environment
    vocabulary.

    * ``files`` — ``{relative/path: contents}`` staged into ``/workspace`` before
      the trial (a fixture tree, a starter file). BOTH engines honor it.
    * ``env`` — ``{NAME: VALUE}`` injected into the container, AFTER the
      provider-key env and never overriding it. **Never secrets** — provider keys
      flow only through ``run.config.yaml`` / per-arm key names, which are
      operational and never locked; ``env`` rides the sha-locked task bytes, so a
      secret here would be committed in plaintext forever.
    * ``extra_hosts`` — per-task egress hosts merged into the *derived* proxy
      allowlist (``harness/run/egress.py``) for ALL arms, so a task can reach an
      extra endpoint without breaking the per-arm "declare for every arm or none"
      symmetry (the extension is task-scoped, applied uniformly to both arms).
    """

    model_config = ConfigDict(extra="forbid")

    files: dict[str, str] = {}
    env: dict[str, str] = {}
    extra_hosts: list[str] = []


# The exact field names the write-side task carries flat; the parity test asserts
# TaskSpec/SDK-Task expose these and no environment field drifts from EnvironmentSpec.
ENVIRONMENT_FIELDS = ("files", "env", "extra_hosts")


class PinnedImage(BaseModel):
    """A built/resolved image and its content-addressed digest [refactor 03 §4].

    ``digest`` is the ``sha256:...`` that pins the image immutably; ``pinned_ref``
    is a runnable reference to exactly those bytes (a ``repo@sha256:...`` for a
    registry image, or the image Id for a local/CI build that was never pushed —
    the same resolution the run engine records in provenance).
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    digest: str
    pinned_ref: str


class ComplianceCheck(BaseModel):
    """One named obligation ``verify`` evaluated [refactor 03 §4]."""

    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    detail: str = ""


class ComplianceReport(BaseModel):
    """The result of ``verify`` — a list of named checks and the overall verdict.

    ``ok`` is the conjunction of every check, computed here so a caller cannot
    forget a failing check. The first failing check's name + detail is the loud,
    named reason a non-compliant image is rejected (never a bare boolean).
    """

    model_config = ConfigDict(extra="forbid")

    image_ref: str
    expected_format: str
    checks: list[ComplianceCheck]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def first_failure(self) -> Optional[ComplianceCheck]:
        return next((c for c in self.checks if not c.ok), None)
