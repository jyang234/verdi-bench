"""Build and pin trial images [refactor 03 §4].

``build(spec)`` builds a plain docker context (satisfying a ``FROM verdi-base``
dependency first) and pins the result to a sha256; ``resolve_digest(ref)`` pins a
ref that already exists locally. All Docker mechanics go through
:class:`harness.hermetic.DockerClient` — this subsystem *consumes* the run engine's
docker layer, never spawning its own processes and never naming the engine itself,
so the AST seam sweep and the import contracts stay green [refactor 03 §7].
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from harness.hermetic import DockerClient

from ..errors import VerdiRefusal
from .registry import BASE_CONTEXT, BASE_TAG
from .spec import ImageSpec, PinnedImage

# Generous: a stack image installs a Node/CLI toolchain at build time (with
# network); the base and the stdlib-only images build in seconds.
BUILD_TIMEOUT_S = 1800
_INSPECT_TIMEOUT_S = 30


class ImageBuildError(VerdiRefusal, RuntimeError):
    """A ``docker build`` failed — surfaced loudly with the builder's stderr."""


class ImageResolveError(VerdiRefusal, RuntimeError):
    """An image ref could not be pinned to a digest (absent, or unresolvable)."""


def _inspect(docker: DockerClient, ref: str, fmt: str) -> Optional[str]:
    """One ``docker inspect --format`` field, or ``None`` if the image is absent."""
    try:
        out = docker.run(
            ["docker", "inspect", "--format", fmt, ref], timeout_s=_INSPECT_TIMEOUT_S
        )
    except (OSError, RuntimeError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _resolve_pinned(docker: DockerClient, ref: str) -> tuple[str, str]:
    """The runnable IMMUTABLE ref and its digest, mirroring the run engine's rule.

    A ``@sha256:`` ref is already pinned. A registry image carries a RepoDigest
    (``repo@sha256:...``, itself runnable). A local/CI image built but never pushed
    has no RepoDigest, so it pins to its content-addressed image Id (also runnable)
    — the same fallback provenance records, so build-then-run is byte-identical.
    """
    if "@sha256:" in ref:
        return ref, ref.split("@", 1)[1]
    repo = _inspect(docker, ref, "{{index .RepoDigests 0}}")
    if repo and "@" in repo:
        return repo, repo.split("@", 1)[1]
    idv = _inspect(docker, ref, "{{.Id}}")
    if idv and idv.startswith("sha256:"):
        return idv, idv
    raise ImageResolveError(
        f"image {ref!r} could not be pinned to a sha256 digest — it is absent "
        "locally or has no resolvable Id (build it first, or check the ref)"
    )


def resolve_digest(ref: str, *, docker: Optional[DockerClient] = None) -> str:
    """The sha256 digest that pins ``ref`` immutably [refactor 03 §4]."""
    return _resolve_pinned(docker or DockerClient(), ref)[1]


def _extends_base(dockerfile: Path) -> bool:
    """True if the context's Dockerfile has a ``FROM verdi-base`` stage."""
    for raw in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper() == "FROM":
            base = parts[1].split(":", 1)[0].split("@", 1)[0]
            if base == BASE_TAG:
                return True
    return False


def _docker_build(docker: DockerClient, tag: str, context: Path) -> None:
    """Run ``docker build -t <tag> <context>`` and fail loudly on nonzero exit."""
    try:
        proc = docker.run(
            ["docker", "build", "-t", tag, str(Path(context).resolve())],
            timeout_s=BUILD_TIMEOUT_S,
        )
    except Exception as e:  # DockerClient propagates TimeoutExpired / OSError
        if type(e).__name__ == "TimeoutExpired":
            raise ImageBuildError(
                f"docker build of {context} for {tag} exceeded {BUILD_TIMEOUT_S}s"
            ) from e
        raise ImageBuildError(f"docker build of {context} for {tag} could not run: {e}") from e
    if proc.returncode != 0:
        raise ImageBuildError(
            f"docker build of {context} for {tag} failed (exit {proc.returncode}):\n"
            + (proc.stderr or proc.stdout or "").strip()[-2000:]
        )


def _ensure_base(docker: DockerClient, context: Path) -> None:
    """Build ``verdi-base`` first when ``context`` extends it [refactor 03 §3].

    Docker's layer cache makes the rebuild cheap when nothing changed; skipped when
    the target IS the base or does not extend it.
    """
    context = Path(context).resolve()
    if context == BASE_CONTEXT.resolve():
        return
    dockerfile = context / "Dockerfile"
    if dockerfile.exists() and _extends_base(dockerfile):
        _docker_build(docker, BASE_TAG, BASE_CONTEXT)


def build(spec: ImageSpec, *, docker: Optional[DockerClient] = None) -> PinnedImage:
    """Build ``spec``'s context (if any) and pin the result [refactor 03 §4].

    ``build_context=None`` pins an already-present ref without building. Otherwise
    the base dependency is satisfied, the context is built and tagged ``spec.ref``,
    and the built image is pinned to its sha256. A build or resolve failure raises
    loudly — a silently-unpinned image would defeat the digest-pinning claim.
    """
    docker = docker or DockerClient()
    if spec.build_context is not None:
        _ensure_base(docker, spec.build_context)
        _docker_build(docker, spec.ref, Path(spec.build_context))
    pinned_ref, digest = _resolve_pinned(docker, spec.ref)
    return PinnedImage(ref=spec.ref, digest=digest, pinned_ref=pinned_ref)
