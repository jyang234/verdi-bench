"""Shared fixture builders [master plan §7.6].

Fabricate a miniature experiment (valid yaml, deterministic ledger context) so
each story's fixture ACs compose instead of hand-rolling ledgers. Fault
injection is via dependency-injected clock/actor.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import yaml

from harness.ledger.events import EventContext
from harness.ledger.identity import derive_experiment_id

_counter = itertools.count()


# --- richer fixtures: locked experiments with trials + grades ---------------
def locked_experiment(dirpath, *, ctx=None, **overrides):
    """Write + lock a miniature experiment; return ``(spec, spec_path, ledger)``.

    Uses a tiny MDE simulation (fast, deterministic) so tests that need a real
    ``experiment_locked`` event — with its MDE block — don't pay the full power
    sweep. Shared across EVAL-6/7/9 fixtures.

    The default lock ctx stamps ``experiment_id`` = the experiment directory's
    name via :func:`ctx_for` (the production derivation seam), mirroring the
    real-world invariant plan enforces (a lock's id is the resolved experiment
    directory [ux-friction AC-1]) so a locked fixture models a real ledger —
    status/scan headers title from that id [ux-friction AC-5].
    ``rich_experiment`` already runs/grades under this same dir-name ctx; the
    default keeps the lock event consistent with it.
    """
    from harness.plan.lock import lock_experiment

    dirpath = Path(dirpath)
    dirpath.mkdir(parents=True, exist_ok=True)
    spec_path = write_experiment_yaml(dirpath / "experiment.yaml", **overrides)
    ledger = dirpath / "ledger.ndjson"
    ctx = ctx or ctx_for(dirpath)
    outcome = lock_experiment(
        spec_path, ledger, ctx=ctx, n_sim=8, n_boot=40, deltas=[0.2, 0.4]
    )
    return outcome.spec, spec_path, ledger


def seed_trial_and_grade(
    ledger,
    ctx,
    *,
    trial_id,
    task_id,
    arm,
    repetition=0,
    passed=True,
    telemetry=None,
    provenance=None,
    flags=None,
    egress_violation=False,
    assertions=None,
):
    """Append one trial record + its grade event, matching the real schemas.

    ``assertions`` overrides the default single holdout assertion — e.g. a
    mixed pass/fail list whose pass *count* diverges from ``binary_score``, the
    shape that makes the content-based fake judge disagree with the
    deterministic winner (EVAL-7's mandatory-review stratum).
    """
    from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
    from harness.ledger.events import record_grade, record_trial

    tel = Telemetry(**(telemetry or {}))
    prov = Provenance(**(provenance or {}))
    flag_obj = Flags(egress_violation=egress_violation, **(flags or {}))
    rec = TrialRecord.assemble(
        trial_id=trial_id,
        task_id=task_id,
        arm=arm,
        repetition=repetition,
        outcome=Outcome.completed,
        telemetry=tel,
        provenance=prov,
        flags=flag_obj,
        artifacts_path=f"/tmp/{trial_id}/artifacts",
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    record_grade(
        ledger,
        ctx,
        trial_id=trial_id,
        task_sha=f"sha-{task_id}",
        assertions=assertions if assertions is not None else [
            {"id": "h1", "source": "holdout_test",
             "result": "pass" if passed else "fail"}],
        binary_score=passed,
    )
    return rec


def fixed_ctx(experiment_id: str, actor: str = "tester") -> EventContext:
    """Deterministic EventContext: monotonic synthetic timestamps, fixed actor.

    ``experiment_id`` is required — the old invisible ``"exp-fixture"`` default
    is gone (human ruling 2026-07-07, Batch A review): a test with a real
    experiment directory derives the id from it via :func:`ctx_for`; a
    filesystem-less unit test passes a visible literal at the call site.
    """
    seq = itertools.count()

    def clock() -> str:
        return f"2026-01-01T00:00:{next(seq):02d}+00:00"

    return EventContext(experiment_id=experiment_id, actor=actor, clock=clock)


def ctx_for(dirpath, actor: str = "tester") -> EventContext:
    """Deterministic ctx whose ``experiment_id`` derives from ``dirpath`` through
    the SAME production seam every stage uses
    (:func:`harness.ledger.identity.derive_experiment_id`), so a fixture ledger
    models the dir-derived identity invariant plan enforces [ux-friction AC-1].
    """
    return fixed_ctx(derive_experiment_id(dirpath), actor=actor)


def valid_experiment_dict(**overrides) -> dict:
    # Derived from the ONE canonical starter template [refactor 02 §2]: the
    # example spec lives in exactly one file, and this fixture parses it rather
    # than keeping a divergent copy. The template's fields are chosen to equal
    # this fixture's historical base, so parsing it is byte-neutral for the many
    # callers that pin the produced spec (test_starter_template_single_source
    # asserts the equality). Tests import the sdk freely (the leaf contract
    # governs harness.*, not tests.*).
    from harness.sdk import starter_spec_text

    base = yaml.safe_load(starter_spec_text())
    base.update(overrides)
    return base


def write_experiment_yaml(path: Path, **overrides) -> Path:
    path = Path(path)
    data = valid_experiment_dict(**overrides)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    # D-P7-6: the rubric is part of the pre-registration — lock now commits its
    # content hash and refuses to lock when the file is absent. Materialize the
    # referenced rubric so a fixture-locked experiment has a committable rubric
    # by default (tests that exercise the absent/swapped rubric remove or rewrite
    # it explicitly).
    rubric_rel = (data.get("judge") or {}).get("rubric")
    if rubric_rel:
        rubric_path = path.parent / rubric_rel
        if not rubric_path.exists():
            rubric_path.parent.mkdir(parents=True, exist_ok=True)
            rubric_path.write_text("Judge on correctness.\n", encoding="utf-8")
    return path
