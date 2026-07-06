"""Plan-stage lock [EVAL-3 AC-2, AC-4, D004].

``lock_experiment`` = validate → ``mde_check`` → sha256 the yaml bytes → append
the ``experiment_locked`` genesis event. ``assert_lock`` is the one helper every
later stage entrypoint (EVAL-4/5/2/6/7/9) calls first: it recomputes the sha and
refuses on mismatch, printing recorded vs computed hashes. A mutated
``experiment.yaml`` cannot be run, graded, or analyzed.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from ..adapters import known_platforms
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, find_events
from ..schema.experiment import ExperimentSpec
from .power import AssumedVariance, MdeReport, VarianceSource, mde_check


class LockError(RuntimeError):
    """Base for lock-stage failures."""


class UnderpoweredError(LockError):
    """hypothesized_effect < mde and the run was not acknowledged."""


class LockMismatchError(LockError):
    """The spec on disk no longer matches the locked sha [AC-2]."""


class AlreadyLockedError(LockError):
    """A lock already exists for this ledger; re-lock is refused [PL-3]."""


class UnknownArmPlatformError(LockError):
    """An arm names a platform with no registered telemetry adapter.

    ``run_trial`` resolves ``get_adapter(arm.platform)`` on every trial, so an
    unregistered platform is unrunnable: each of that arm's cells would fail
    closed mid-run as ``trial_infra_failed(unknown_platform)`` [RN-15], after
    real spend on the other arm. Refuse at plan time instead."""


class RubricCommitmentError(LockError):
    """The spec names a judge rubric whose file is absent [D-P7-6].

    The rubric is part of the pre-registration — the judging instrument — so a
    spec cannot be locked without the rubric present to commit its content
    hash. Refuse rather than lock a dangling reference."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spec_sha256(spec_path) -> str:
    """sha256 over the yaml file's exact bytes."""
    return _sha256_bytes(Path(spec_path).read_bytes())


@dataclass
class LockOutcome:
    spec: ExperimentSpec
    spec_sha256: str
    mde: dict  # the mde payload embedded in the lock event (today's keys) [seam]
    event: dict
    mde_report: MdeReport  # the typed power result the payload was rendered from


# --- preflight steps [refactor 02 §4] --------------------------------------
# ``lock_experiment`` is decomposed into these independently callable, typed
# steps so the same list can be composed by the lock (inside the flock) and by
# the author preview (a pure read) — parity by construction. Each raises the one
# ``LockError`` subtype its job owns; none writes to the ledger.


@dataclass
class ParsedSpec:
    """The spec parsed from the very bytes whose sha is recorded [PL-2]."""

    spec: ExperimentSpec
    spec_bytes: bytes
    sha256: str


def parse_and_hash_spec(spec_path) -> ParsedSpec:
    """Preflight: read the spec bytes once, then parse *and* hash the same buffer.

    PL-2: the recorded sha is provably the sha of the validated content — no
    window for the file to change between the parse read and a second hash read.
    Raises ``SpecError`` (pre-registration refusal) when the shape is invalid.
    """
    spec_bytes = Path(spec_path).read_bytes()
    spec = ExperimentSpec.from_yaml_text(spec_bytes.decode("utf-8"), source=str(spec_path))
    return ParsedSpec(spec=spec, spec_bytes=spec_bytes, sha256=_sha256_bytes(spec_bytes))


def check_arm_platforms(spec: ExperimentSpec) -> None:
    """Preflight: every arm platform must have a registered telemetry adapter.

    ``run_trial`` resolves ``get_adapter(arm.platform)`` on every trial, so an
    unregistered platform is unrunnable (see ``UnknownArmPlatformError``). The
    schema stays a pure shape contract; whether this environment can *run* a
    platform is a capability check, so it lives at lock — like the rubric-presence
    check. Composed identically by the author preview so a green preview cannot
    then refuse at lock.
    """
    registered = known_platforms()
    unknown = [(a.name, a.platform) for a in spec.arms if a.platform not in registered]
    if unknown:
        listed = ", ".join(f"arm {n!r} has platform {p!r}" for n, p in unknown)
        raise UnknownArmPlatformError(
            f"{listed}: no registered telemetry adapter; registered platforms: "
            f"{registered}. Add an adapter in harness/adapters/ "
            "(docs/deep-dive.md §7) or use a registered platform."
        )


def check_chain_integrity(ledger_path) -> None:
    """Preflight: verify the existing chain before trusting or appending to it.

    7A-3: an absent/empty ledger is the fresh-experiment path (``assert_chain`` is
    silent there); a pre-existing tampered or truncated ledger is refused
    (``ChainIntegrityError``) rather than chained onto. A ledger-state check, so
    the author preview of an unlocked draft legitimately skips it.
    """
    assert_chain(ledger_path)


def check_single_lock(ledger_path) -> None:
    """Preflight *and* the flock-internal recheck: refuse a second lock [PL-3].

    A re-lock would append a second ``experiment_locked`` event while
    ``assert_lock`` keys the first, telling the operator a spec is locked when it
    isn't. One lock per ledger, period. This single definition is called twice by
    ``lock_experiment`` — once as an early-refusal preflight step and once inside
    the flock as the authoritative recheck (PRA-M3) — so the message lives in one
    place. A ledger-state check, so the author preview skips it.
    """
    if find_events(ledger_path, events.EXPERIMENT_LOCKED):
        raise AlreadyLockedError(
            f"{ledger_path} already has an experiment_locked event; re-lock is "
            "refused. Start a fresh ledger to re-plan."
        )


def run_power_gate(
    spec: ExperimentSpec,
    variance_source: VarianceSource,
    *,
    n_task_clusters: Optional[int],
    acknowledge_underpowered: bool,
    mde_kwargs: dict,
) -> tuple[MdeReport, Optional[dict]]:
    """Preflight: compute power and enforce the underpowered gate [PL-1, D001].

    Returns the ``MdeReport`` and the inline acknowledgment payload (``None``
    unless an underpowered design was locked with acknowledgment [PL-14]).

    PL-1 + D-P5-4: power is computed at the design's real size — the corpus's
    task-*cluster* count, with ``repetitions`` correlated reps per task — when the
    task source is available, not the variance source's calibration n_tasks. The
    power sim clusters by task and resamples clusters, the same variance model
    EVAL-6's analysis uses, so the pre-registration power model and the
    realized-data analysis cannot disagree.

    Underpowered check [D001, AC-4]: refuse unless acknowledged. A ``None`` MDE
    means the design could not detect *any* swept effect — the maximally
    underpowered case — so it must NOT fail open: treat it as underpowered.
    Omitting ``hypothesized_effect`` skips the gate entirely; the skip is recorded
    as a lock-stage ``power_gate_skipped`` flag on the report (folded into the
    event by ``to_event_payload``), so it is a ledgered decision, not a silent
    no-check — and *not* an in-place mutation of power's return.
    """
    report = mde_check(spec, variance_source, n_tasks=n_task_clusters, **mde_kwargs)
    ack_payload: Optional[dict] = None
    if spec.hypothesized_effect is not None:
        mde_val = report.mde
        underpowered = mde_val is None or spec.hypothesized_effect < mde_val
        if underpowered:
            mde_desc = (
                "incomputable (no swept effect reached target power)"
                if mde_val is None
                else str(mde_val)
            )
            if not acknowledge_underpowered:
                raise UnderpoweredError(
                    f"hypothesized_effect {spec.hypothesized_effect} vs MDE "
                    f"{mde_desc}: design is underpowered. Re-run with "
                    "acknowledge_underpowered=True to lock with a ledgered "
                    "acknowledgment."
                )
            ack_payload = {
                "mde": mde_val,
                "hypothesized_effect": spec.hypothesized_effect,
            }
    else:
        report = replace(report, power_gate_skipped=True)
    return report, ack_payload


def commit_rubric(spec_path, spec: ExperimentSpec) -> str:
    """Preflight: commit the judging rubric's content hash into the lock [D-P7-6].

    The rubric is part of the pre-registration, so an absent rubric file refuses
    the lock; else the hash is the same normalized-text hash the verdict
    provenance carries (judge/packet.py), so lock ↔ verdict comparability is exact
    and CRLF-checkout drift is a non-event.
    """
    rubric_path = Path(spec_path).parent / spec.judge.rubric
    if not rubric_path.is_file():
        raise RubricCommitmentError(
            f"judge rubric {spec.judge.rubric!r} not found at {rubric_path}; the "
            "rubric is part of the pre-registration and must be present to lock "
            "[D-P7-6]"
        )
    return _sha256_bytes(rubric_path.read_text(encoding="utf-8").encode("utf-8"))


def commit_tasks(spec: ExperimentSpec, task_dicts: Optional[list]) -> Optional[dict]:
    """Preflight: pin the task-content commitment [PL-7, D-6].

    So run/grade can refuse a post-lock swap of prompts, canaries, holdout
    scripts, or scoring. ``None`` when the plan flow carries no task source.
    Delegates to ``corpus.commit.compute_commitment`` — never reimplements it.
    """
    if not task_dicts:
        return None
    from ..corpus.commit import compute_commitment

    return compute_commitment(
        task_dicts, corpus_id=spec.corpus.id, semver=spec.corpus.version
    )


def lock_experiment(
    spec_path,
    ledger_path,
    *,
    ctx: EventContext,
    variance_source: Optional[VarianceSource] = None,
    acknowledge_underpowered: bool = False,
    attested_by: Optional[str] = None,
    attestation_method: str = "anchor-plus-actor-v1",
    task_dicts: Optional[list] = None,
    **mde_kwargs,
) -> LockOutcome:
    """Validate, power-check, and write the genesis lock event.

    PRA-L2: ``attested_by`` defaults to the resolved actor on ``ctx`` (itself
    produced by ``resolve_actor``, which refuses rather than record ``"unknown"``)
    — never the literal ``"unknown"`` sentinel ``actor.py`` exists to ban. The
    method is ``anchor-plus-actor-v1``; it does not claim a cryptographic
    attestation the instrument does not yet provide (contrast the real Ed25519
    curation-approval signatures).
    """
    attested_by = attested_by or ctx.actor

    # Preflight steps, composed in order (same list the author preview composes).
    # Each is independently callable and raises the one LockError its job owns.
    parsed = parse_and_hash_spec(spec_path)          # 1. spec-parse + hash [PL-2]
    spec = parsed.spec
    sha = parsed.sha256
    check_arm_platforms(spec)                         # 2. platform capability
    check_chain_integrity(ledger_path)                # 3. chain integrity [7A-3]
    check_single_lock(ledger_path)                    # 4. single-lock (outer) [PL-3]

    if variance_source is None:
        variance_source = AssumedVariance()
    n_task_clusters = len(task_dicts) if task_dicts else None
    report, ack_payload = run_power_gate(             # 5. power gate [PL-1, D001]
        spec,
        variance_source,
        n_task_clusters=n_task_clusters,
        acknowledge_underpowered=acknowledge_underpowered,
        mde_kwargs=mde_kwargs,
    )
    rubric_sha256 = commit_rubric(spec_path, spec)    # 6. rubric commitment [D-P7-6]
    task_commitment = commit_tasks(spec, task_dicts)  # 7. task commitment [PL-7]

    # The mde payload the event embeds: today's keys, with the lock-stage
    # power_gate_skipped flag folded in — never a mutation of power's return.
    mde_payload = report.to_event_payload()

    # PRA-M3: serialize concurrent `bench plan` on this ledger so the
    # check-then-append is atomic. Without the guard two concurrent invocations
    # both pass the outer single-lock check and both append experiment_locked;
    # assert_lock now refuses the resulting >1-lock ledger, but preventing it here
    # is cleaner. Use a separate lock file — flock on the ledger fd itself would
    # deadlock against append_event's own flock inside record_experiment_locked.
    # PL-14: the lock is the single genesis event; any underpowered acknowledgment
    # rides inline on it (ack_payload), so one attempted operation ⇒ one event.
    guard = Path(str(ledger_path) + ".planlock")
    guard.parent.mkdir(parents=True, exist_ok=True)
    gfd = os.open(guard, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(gfd, fcntl.LOCK_EX)
        check_single_lock(ledger_path)  # authoritative recheck inside the flock
        event = events.record_experiment_locked(
            ledger_path,
            ctx,
            spec_sha256=sha,
            spec_path=str(spec_path),
            seed=spec.seed,
            mde=mde_payload,
            attested_by=attested_by,
            method=attestation_method,
            task_commitment=task_commitment,
            acknowledged_underpowered=ack_payload,
            rubric_sha256=rubric_sha256,
        )
    finally:
        try:
            fcntl.flock(gfd, fcntl.LOCK_UN)
        finally:
            os.close(gfd)
    return LockOutcome(
        spec=spec, spec_sha256=sha, mde=mde_payload, event=event, mde_report=report
    )


@dataclass
class LockView:
    """The verified lock event plus the spec parsed from the very bytes whose sha
    was checked — so a caller never re-reads the file [PRA-M1]."""

    event: dict
    spec: ExperimentSpec


def assert_lock(spec_path, ledger_path) -> LockView:
    """Refuse to proceed unless the on-disk spec matches the locked sha [AC-2].

    Returns the ``experiment_locked`` event and the parsed spec. Every later
    stage entrypoint calls this first, so a post-lock mutation of
    ``experiment.yaml`` fails every downstream operation closed.

    PRA-M1: the spec bytes are read *once*, then hashed and parsed from the same
    buffer, and the parsed spec is returned. Consumers must use the returned spec
    rather than a second independent ``ExperimentSpec.from_yaml(spec_path)`` — a
    second read reopened a TOCTOU window where a spec swapped between the sha
    check and the re-read would run under an unlocked spec while the ledger
    attests the locked one. This mirrors ``lock_experiment``'s PL-2 discipline.
    """
    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    if not locks:
        raise LockMismatchError(
            f"no experiment_locked event in {ledger_path}; run `bench plan` first"
        )
    # PRA-M3: exactly one lock per ledger. More than one means a concurrent
    # double-lock slipped past lock_experiment's check-then-append window; keying
    # locks[0] would silently attest one spec while another lock sits unnoticed.
    # Refuse loudly rather than pick one.
    if len(locks) > 1:
        raise LockMismatchError(
            f"{ledger_path} has {len(locks)} experiment_locked events; a ledger "
            "must carry exactly one lock [PL-3]. Start a fresh ledger to re-plan."
        )
    # An empty/absent ledger is "not planned yet" (handled above); once a lock
    # exists we verify the whole chain before trusting any recorded field, so a
    # rewritten lock line cannot pass this gate on a forged sha [PL-6].
    assert_chain(ledger_path)
    spec_bytes = Path(spec_path).read_bytes()
    recorded = locks[0]["spec_sha256"]
    computed = _sha256_bytes(spec_bytes)
    if recorded != computed:
        raise LockMismatchError(
            "experiment.yaml has changed since it was locked:\n"
            f"  recorded sha256: {recorded}\n"
            f"  computed sha256: {computed}\n"
            "the primary metric and decision rule are immutable post-lock"
        )
    spec = ExperimentSpec.from_yaml_text(
        spec_bytes.decode("utf-8"), source=str(spec_path)
    )
    return LockView(event=locks[0], spec=spec)


# --- one-event property registration [EVAL-3 §M7] --------------------------
def _plan_lock_entrypoint(ctx_dir: str) -> None:
    from ..entrypoints import register_entrypoint  # noqa: F401 (kept local)
    from ..ledger.events import EventContext

    d = Path(ctx_dir)
    lock_experiment(
        d / "experiment.yaml",
        d / "ledger.ndjson",
        ctx=EventContext(experiment_id="prop"),
        n_sim=8,
        n_boot=40,
        deltas=[0.2, 0.4],
    )


def _prepare_underpowered(ctx_dir: str) -> None:
    # Make the fixture experiment underpowered (hypothesized_effect below any
    # reasonable MDE). Not a ledger write — just fixture prep, so the sweep still
    # measures only the lock event fn appends.
    import yaml

    p = Path(ctx_dir) / "experiment.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["hypothesized_effect"] = 0.001
    p.write_text(yaml.safe_dump(data), encoding="utf-8")


def _plan_lock_underpowered_entrypoint(ctx_dir: str) -> None:
    # PL-14: the acknowledged-underpowered path must also be one event. Lock the
    # (now underpowered) design with acknowledgment; the acknowledgment rides
    # inline on the single lock event instead of a second event.
    from ..ledger.events import EventContext
    from .power import AssumedVariance

    d = Path(ctx_dir)
    lock_experiment(
        d / "experiment.yaml",
        d / "ledger.ndjson",
        ctx=EventContext(experiment_id="prop"),
        variance_source=AssumedVariance(p=0.5, rho=0.3, n_tasks=20),
        acknowledge_underpowered=True,
        n_sim=8,
        n_boot=40,
        deltas=[0.2, 0.4],
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("plan-lock", _plan_lock_entrypoint)
    register_entrypoint(
        "plan-lock-underpowered",
        _plan_lock_underpowered_entrypoint,
        prepare=_prepare_underpowered,
    )


_register()
