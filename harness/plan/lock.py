"""Plan-stage lock [EVAL-3 AC-2, AC-4, D004].

``lock_experiment`` = validate → ``mde_check`` → sha256 the yaml bytes → append
the ``experiment_locked`` genesis event. ``assert_lock`` is the one helper every
later stage entrypoint (EVAL-4/5/2/6/7/9) calls first: it recomputes the sha and
refuses on mismatch, printing recorded vs computed hashes. A mutated
``experiment.yaml`` cannot be run, graded, or analyzed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..adapters import known_platforms
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, find_events
from ..schema.experiment import ExperimentSpec
from .power import AssumedVariance, VarianceSource, mde_check


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
    mde: dict
    event: dict


def lock_experiment(
    spec_path,
    ledger_path,
    *,
    ctx: EventContext,
    variance_source: Optional[VarianceSource] = None,
    acknowledge_underpowered: bool = False,
    attested_by: str = "unknown",
    attestation_method: str = "anchor-plus-attestation-v1",
    task_dicts: Optional[list] = None,
    **mde_kwargs,
) -> LockOutcome:
    """Validate, power-check, and write the genesis lock event."""
    # PL-2: read the spec bytes once, then parse *and* hash the same buffer, so
    # the recorded sha is provably the sha of the validated content — no window
    # for the file to change between the parse read and a second hash read.
    spec_bytes = Path(spec_path).read_bytes()
    spec = ExperimentSpec.from_yaml_text(
        spec_bytes.decode("utf-8"), source=str(spec_path)
    )
    sha = _sha256_bytes(spec_bytes)

    # Every arm platform must have a registered telemetry adapter, or the arm
    # is unrunnable (see UnknownArmPlatformError). The schema stays a pure
    # shape contract; whether this environment can *run* a platform is a
    # capability check, so it lives at lock — like the rubric-presence check.
    registered = known_platforms()
    unknown = [(a.name, a.platform) for a in spec.arms if a.platform not in registered]
    if unknown:
        listed = ", ".join(f"arm {n!r} has platform {p!r}" for n, p in unknown)
        raise UnknownArmPlatformError(
            f"{listed}: no registered telemetry adapter; registered platforms: "
            f"{registered}. Add an adapter in harness/adapters/ "
            "(docs/deep-dive.md §7) or use a registered platform."
        )

    # 7A-3: before trusting anything the ledger already contains (the lock-count
    # check below, or any later append onto it), verify its chain. An
    # absent/empty ledger is the fresh-experiment path — assert_chain is silent
    # there — but a pre-existing tampered or truncated ledger is refused rather
    # than chained onto.
    assert_chain(ledger_path)

    # PL-3: refuse a second lock. A re-lock would append a second
    # experiment_locked event while assert_lock keys the first, telling the
    # operator a spec is locked when it isn't. One lock per ledger, period.
    if find_events(ledger_path, events.EXPERIMENT_LOCKED):
        raise AlreadyLockedError(
            f"{ledger_path} already has an experiment_locked event; re-lock is "
            "refused. Start a fresh ledger to re-plan."
        )

    if variance_source is None:
        variance_source = AssumedVariance()
    # PL-1 + D-P5-4: compute power at the design's real size — the corpus's
    # task-*cluster* count, with ``repetitions`` correlated reps per task — when the
    # task source is available, not the variance source's calibration n_tasks
    # (default 50, which ignored the actual design). The power sim clusters by task
    # and resamples clusters, the same variance model EVAL-6's analysis uses, so the
    # pre-registration power model and the realized-data analysis cannot disagree.
    # When repetitions > 1 the correlated reps carry less information than
    # independent observations, so the MDE is honestly larger (no longer optimistic).
    n_task_clusters = len(task_dicts) if task_dicts else None
    mde = mde_check(spec, variance_source, n_tasks=n_task_clusters, **mde_kwargs)

    # Underpowered check [D001, AC-4]: refuse unless acknowledged. A None MDE
    # means the design could not detect *any* swept effect — the maximally
    # underpowered case — so it must NOT fail open: treat it as underpowered.
    ack_underpowered = False
    mde_val = None
    if spec.hypothesized_effect is not None:
        mde_val = mde["mde"]
        underpowered = mde_val is None or spec.hypothesized_effect < mde_val
        if underpowered:
            mde_desc = "incomputable (no swept effect reached target power)" if mde_val is None else str(mde_val)
            if not acknowledge_underpowered:
                raise UnderpoweredError(
                    f"hypothesized_effect {spec.hypothesized_effect} vs MDE "
                    f"{mde_desc}: design is underpowered. Re-run with "
                    "acknowledge_underpowered=True to lock with a ledgered "
                    "acknowledgment."
                )
            ack_underpowered = True
    else:
        # PL-1: omitting hypothesized_effect skips the power gate entirely. Record
        # that it was skipped so it is a ledgered decision, not a silent no-check.
        if "power_gate_skipped" not in mde["flags"]:
            mde["flags"].append("power_gate_skipped")

    # D-P7-6: commit the judging rubric's content hash into the lock. The rubric
    # is part of the pre-registration, so an absent rubric file refuses the lock;
    # else the hash is the same normalized-text hash the verdict provenance
    # carries (judge/packet.py), so lock ↔ verdict comparability is exact and
    # CRLF-checkout drift is a non-event.
    rubric_path = Path(spec_path).parent / spec.judge.rubric
    if not rubric_path.is_file():
        raise RubricCommitmentError(
            f"judge rubric {spec.judge.rubric!r} not found at {rubric_path}; the "
            "rubric is part of the pre-registration and must be present to lock "
            "[D-P7-6]"
        )
    rubric_sha256 = _sha256_bytes(
        rubric_path.read_text(encoding="utf-8").encode("utf-8")
    )

    # PL-7 / D-6: pin the task-content commitment so run/grade can refuse a
    # post-lock swap of prompts, canaries, holdout scripts, or scoring.
    task_commitment = None
    if task_dicts:
        from ..corpus.commit import compute_commitment

        task_commitment = compute_commitment(
            task_dicts, corpus_id=spec.corpus.id, semver=spec.corpus.version
        )

    # PL-14: the lock is the single genesis event. An underpowered acknowledgment
    # rides *inline* on the lock event (not a second event), so locking an
    # acknowledged-underpowered design is one attempted operation ⇒ one event,
    # and the lock stays the chain genesis (prev_hash all-zeros).
    ack_payload = (
        {"mde": mde_val, "hypothesized_effect": spec.hypothesized_effect}
        if ack_underpowered
        else None
    )
    event = events.record_experiment_locked(
        ledger_path,
        ctx,
        spec_sha256=sha,
        spec_path=str(spec_path),
        seed=spec.seed,
        mde=mde,
        attested_by=attested_by,
        method=attestation_method,
        task_commitment=task_commitment,
        acknowledged_underpowered=ack_payload,
        rubric_sha256=rubric_sha256,
    )
    return LockOutcome(spec=spec, spec_sha256=sha, mde=mde, event=event)


def assert_lock(spec_path, ledger_path) -> dict:
    """Refuse to proceed unless the on-disk spec matches the locked sha [AC-2].

    Returns the ``experiment_locked`` event on success. Every later stage
    entrypoint calls this first, so a post-lock mutation of ``experiment.yaml``
    fails every downstream operation closed.
    """
    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    if not locks:
        raise LockMismatchError(
            f"no experiment_locked event in {ledger_path}; run `bench plan` first"
        )
    # An empty/absent ledger is "not planned yet" (handled above); once a lock
    # exists we verify the whole chain before trusting any recorded field, so a
    # rewritten lock line cannot pass this gate on a forged sha [PL-6].
    assert_chain(ledger_path)
    recorded = locks[0]["spec_sha256"]
    computed = spec_sha256(spec_path)
    if recorded != computed:
        raise LockMismatchError(
            "experiment.yaml has changed since it was locked:\n"
            f"  recorded sha256: {recorded}\n"
            f"  computed sha256: {computed}\n"
            "the primary metric and decision rule are immutable post-lock"
        )
    return locks[0]


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
