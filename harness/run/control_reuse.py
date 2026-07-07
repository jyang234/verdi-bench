"""Control-run reuse: the fingerprint and its preflight gate [control-reuse plan].

A reused control arm is only comparable to a freshly-run contender if everything
that could change the control's behavior or its grading is byte-identical. This
module composes that identity — the *control fingerprint* — out of primitives the
instrument already computes, and provides the preflight comparison that refuses
reuse loudly on any drift, naming the component that moved.

Design notes:

* **Gated components** (must match byte-for-byte): the per-task content sha
  (prompt, image ref, plugin ids, canaries, holdouts_dir path — everything in the
  ``tasks.yaml`` entry) and holdout script bytes; the arm definition; the pinned
  operational environment (engine, quotas, egress allowlist); the grader (plugin
  ids + the instrument git sha that versions the grader *code*); and
  ``repetitions``.
* **Audit-only components** (recorded on the bundle, disclosed, NOT gated):
  resolved image *digest* and harbor version. These describe realized infra
  weather; gating on a resolved digest against a declared image ref would
  false-mismatch, so the gate compares declared/reproducible inputs and the
  realized digests are surfaced for human judgment instead.

Pure by construction: no ledger, no network, no LLM client, no wall-clock — so
plan/run compute an identical fingerprint and the import contracts stay green.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..adapters.base import Quotas
from ..corpus.commit import holdout_content_sha, task_content_sha
from ..errors import VerdiRefusal
from ..corpus.public import content_sha
from ..schema.experiment import Arm

# Bumped only on an intentional, disclosed change to what the fingerprint hashes
# or how — so a bundle exported under an older definition can be told apart from
# one that merely drifted. A versioned contract, like every hashed seam here.
# v2 [decision A4, refactor 01 §4 D2]: the grader component's plugin ids are now
# extracted from the documented "plugin_ids" task key (previously the non-doc
# "plugins" key, which left the component blind to plugin drift); bundles
# exported under v1 refuse cleanly with the version-mismatch message.
FINGERPRINT_VERSION = 2


class ControlReuseError(VerdiRefusal, RuntimeError):
    """Base for control-reuse failures."""


class ControlReuseFingerprintError(ControlReuseError):
    """The current experiment's control fingerprint does not match the reused
    bundle's — a task, holdout, arm, environment, grader, or repetitions drift.
    Reuse is refused: a reused control is only valid when provably unchanged."""


def primary_pair_contender(spec, control_arm: str) -> Optional[str]:
    """The contender arm for a reused control: the *other* member of the
    pre-registered primary pair (``spec.arms[0]``/``[1]``), or ``None`` when the
    control is not in that pair.

    The single source of truth for "which arm is the contender" — the judge
    assembly, the analyze reuse section, and the import gate all read it here so
    they cannot drift on the >2-arm case (v1 reuses only a primary-pair control).
    """
    names = [spec.arms[0].name, spec.arms[1].name]
    if control_arm not in names:
        return None
    return names[1] if control_arm == names[0] else names[0]


def _env_component(
    *,
    engine: str,
    quotas: Quotas,
    proxy_allowlist: list[str],
    infra_hosts: list[str],
) -> str:
    """Canonical hash of the pinned operational environment — the declared,
    reproducible inputs (not realized infra digests, which are audit-only)."""
    return content_sha(
        {
            "engine": engine,
            "quotas": {"cpus": quotas.cpus, "mem": quotas.mem},
            "proxy_allowlist": sorted(proxy_allowlist),
            "infra_hosts": sorted(infra_hosts),
        }
    )


def _grader_component(*, plugin_ids: list[str], instrument_git_sha: str) -> str:
    """The grader identity: the declared plugin ids plus the instrument git sha
    that versions the grader *code*. The git sha is deliberately coarse (it moves
    on any instrument commit), so the gate errs toward refusing reuse across
    instrument versions — the fail-safe default for a 'provably unchanged' gate."""
    return content_sha(
        {"plugin_ids": sorted(plugin_ids), "instrument_git_sha": instrument_git_sha}
    )


def _tasks_component(task_dicts: list[dict], experiment_dir: Path) -> dict[str, dict]:
    """Per-task ``{task_id: {content_sha, holdout_sha}}``.

    ``content_sha`` covers the whole ``tasks.yaml`` entry (prompt, image ref,
    plugins, canaries, holdouts_dir path); ``holdout_sha`` covers the on-disk
    holdout bytes the grader mounts — the coverage the lock-time commitment omits.
    """
    out: dict[str, dict] = {}
    for task in task_dicts:
        holdouts_dir = task.get("holdouts_dir")
        holdout_sha = (
            holdout_content_sha(experiment_dir / holdouts_dir)
            if holdouts_dir
            else content_sha({})
        )
        out[task["id"]] = {
            "content_sha": task_content_sha(task),
            "holdout_sha": holdout_sha,
        }
    return out


def compute_fingerprint(
    *,
    arm: Arm,
    task_dicts: list[dict],
    experiment_dir,
    engine: str,
    quotas: Quotas,
    proxy_allowlist: Optional[list[str]] = None,
    infra_hosts: Optional[list[str]] = None,
    repetitions: int,
    plugin_ids: list[str],
    instrument_git_sha: str,
) -> dict:
    """Compose the control fingerprint for one arm over its task set.

    Returns a self-describing dict of named component hashes plus a top-level
    ``digest`` over them, so :func:`assert_fingerprint_match` can name exactly
    which component drifted instead of reporting an opaque hash mismatch.
    """
    experiment_dir = Path(experiment_dir)
    components = {
        "version": FINGERPRINT_VERSION,
        "arm": content_sha(arm.model_dump(mode="json")),
        "tasks": _tasks_component(task_dicts, experiment_dir),
        "env": _env_component(
            engine=engine,
            quotas=quotas,
            proxy_allowlist=proxy_allowlist or [],
            infra_hosts=infra_hosts or [],
        ),
        "grader": _grader_component(
            plugin_ids=plugin_ids, instrument_git_sha=instrument_git_sha
        ),
        "repetitions": repetitions,
    }
    return {**components, "digest": content_sha(components)}


def _tasks_drift(current: dict, recorded: dict) -> list[str]:
    """Human-readable per-task drift lines for the ``tasks`` component."""
    cur, rec = current.get("tasks", {}), recorded.get("tasks", {})
    lines: list[str] = []
    for tid in sorted(set(cur) | set(rec)):
        if tid not in cur:
            lines.append(f"task {tid!r} present in bundle but not in this experiment")
        elif tid not in rec:
            lines.append(f"task {tid!r} present in this experiment but not in bundle")
        elif cur[tid].get("content_sha") != rec[tid].get("content_sha"):
            lines.append(f"task {tid!r} definition (tasks.yaml entry) changed")
        elif cur[tid].get("holdout_sha") != rec[tid].get("holdout_sha"):
            lines.append(f"task {tid!r} holdout script bytes changed")
    return lines


def assert_fingerprint_match(current: dict, recorded: dict) -> None:
    """Refuse reuse unless every gated component matches, naming what drifted.

    A reused control is exploratory-only, but it is still only *meaningful* when
    provably unchanged — so this is a hard refusal, not a disclosure. Fails
    loudly with the specific component(s) that moved so the operator knows why.
    """
    if current.get("version") != recorded.get("version"):
        raise ControlReuseFingerprintError(
            f"fingerprint version mismatch: this instrument computes v"
            f"{current.get('version')}, the bundle recorded v{recorded.get('version')}; "
            "re-export the control bundle with the current instrument"
        )
    drift: list[str] = []
    if current.get("arm") != recorded.get("arm"):
        drift.append("arm definition changed (model / payload / cutoff / aux / hosts)")
    drift.extend(_tasks_drift(current, recorded))
    if current.get("env") != recorded.get("env"):
        drift.append("operational environment changed (engine / quotas / egress allowlist)")
    if current.get("grader") != recorded.get("grader"):
        drift.append("grader changed (plugin ids or instrument version)")
    if current.get("repetitions") != recorded.get("repetitions"):
        drift.append(
            f"repetitions changed ({recorded.get('repetitions')} in bundle, "
            f"{current.get('repetitions')} now)"
        )
    if drift:
        raise ControlReuseFingerprintError(
            "control cannot be reused — it is not provably unchanged since the "
            "bundle was exported:\n  - " + "\n  - ".join(drift)
        )
