"""Workspace evidence commitment [F-H3; the trajectory-sha seam's twin].

The end-state detectors (forensics, contamination) read live workspace bytes;
without a chain commitment those bytes are the one evidence tier "you cannot
quietly edit history" does not cover — history is tamper-evident, the evidence
it points at is not. Grading commits a canonical content hash of the workspace
onto the grade event; both scanners verify it and disclose any mismatch as a
named coverage gap, mirroring :func:`harness.run.trajectory.resolve_trajectory`.

The canonical walk is the judge's hardened solution definition
(``judge.assemble``): sorted, symlink-skipping, escape-confined, excluding the
``artifacts/`` subtree and the grader output — but over RAW BYTES, so binary
files are committed too. Changing the walk changes what the hash covers; bump
``WORKSPACE_WALK_VERSION`` so old and new hashes are never silently compared.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

WORKSPACE_WALK_VERSION = 1

_GRADER_OUTPUT = "holdout_results.json"  # must match grade.runners.HOLDOUT_RESULTS [refactor 13 OI-A]

# Closed status vocabulary — gaps are data, never exceptions [AC-6 discipline].
VERIFIED = "verified"
ABSENT = "absent"                      # no ledgered commitment (legacy chains)
MISSING_WORKSPACE = "missing_workspace"
SHA_MISMATCH = "sha_mismatch"


def _default_artifacts_dir(workspace: Path) -> Path:
    # Both engines construct the artifacts dir as <workspace>/artifacts; the
    # default keeps hashers and verifiers aligned when no explicit path rides in.
    return workspace / "artifacts"


def workspace_sha256(workspace, artifacts_dir=None) -> str:
    """Canonical content hash of a workspace's solution bytes.

    A sorted ``{relpath: sha256(bytes)}`` manifest, hashed under the chain's
    canonical JSON conventions — ordering-independent, binary-safe.
    """
    workspace = Path(workspace)
    artifacts = Path(artifacts_dir) if artifacts_dir else _default_artifacts_dir(workspace)
    ws_real = workspace.resolve()
    manifest: dict[str, str] = {}
    for p in sorted(workspace.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        if not p.resolve().is_relative_to(ws_real):
            continue  # escape-confined, like the judge's walk [PRA-M5]
        if p == artifacts or artifacts in p.parents:
            continue
        if p.name == _GRADER_OUTPUT:
            continue
        manifest[p.relative_to(workspace).as_posix()] = hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_workspace(workspace_root, ledgered_sha: Optional[str], *, artifacts_dir=None) -> str:
    """Verify live workspace bytes against the ledgered commitment [F-H3].

    Returns one of the closed statuses above; never raises on the disk state —
    a verification failure is a named coverage gap for the caller to disclose,
    exactly like a trajectory ``sha_mismatch``.
    """
    if ledgered_sha is None:
        return ABSENT
    if not workspace_root or not Path(workspace_root).is_dir():
        return MISSING_WORKSPACE
    if workspace_sha256(workspace_root, artifacts_dir) != ledgered_sha:
        return SHA_MISMATCH
    return VERIFIED
