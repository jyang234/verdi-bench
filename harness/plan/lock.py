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


def spec_sha256(spec_path) -> str:
    """sha256 over the yaml file's exact bytes."""
    return hashlib.sha256(Path(spec_path).read_bytes()).hexdigest()


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
    **mde_kwargs,
) -> LockOutcome:
    """Validate, power-check, and write the genesis lock event."""
    spec = ExperimentSpec.from_yaml(spec_path)

    if variance_source is None:
        variance_source = AssumedVariance()
    mde = mde_check(spec, variance_source, **mde_kwargs)

    # Underpowered check [D001, AC-4]: refuse unless acknowledged. A None MDE
    # means the design could not detect *any* swept effect — the maximally
    # underpowered case — so it must NOT fail open: treat it as underpowered.
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
            events.record_acknowledged_underpowered(
                ledger_path,
                ctx,
                mde=mde_val,
                hypothesized_effect=spec.hypothesized_effect,
            )

    sha = spec_sha256(spec_path)
    event = events.record_experiment_locked(
        ledger_path,
        ctx,
        spec_sha256=sha,
        spec_path=str(spec_path),
        seed=spec.seed,
        mde=mde,
        attested_by=attested_by,
        method=attestation_method,
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


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("plan-lock", _plan_lock_entrypoint)


_register()
