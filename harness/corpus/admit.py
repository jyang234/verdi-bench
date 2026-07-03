"""Corpus admission gate [EVAL-8 §M4, AC-4, D002].

Admission is two mechanical preconditions, both read from the ledger:

1. a recorded human ``curation_approval`` event for the candidate + task sha, and
2. a ledgered **clean** EVAL-5 flake baseline for that same task sha.

No code path admits a task without both — auto-admission is unrepresentable. A
task that has not been admitted cannot be scheduled (the run scheduler already
refuses quarantined tasks; a pending candidate is excluded by
``CorpusManifest.is_schedulable``).
"""

from __future__ import annotations

from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, find_events
from .registry import CorpusError, CorpusManifest, TaskEntry


class CurationRequiredError(CorpusError):
    """No curation_approval event for the candidate [AC-4]."""


class BaselinePrerequisiteError(CorpusError):
    """Approved but no clean flake baseline for the task sha [AC-4]."""


def has_curation_approval(ledger_path, candidate_id: str, task_sha: str) -> bool:
    for ev in find_events(ledger_path, events.CURATION_APPROVAL):
        if ev["candidate_id"] == candidate_id and ev["task_sha"] == task_sha:
            return True
    return False


def has_clean_baseline(ledger_path, task_sha: str) -> bool:
    """A clean flake baseline exists for this exact task sha (latest wins).

    Baselines are keyed by task sha here (not task id): admission binds to the
    *version* that was reviewed, so a later re-baseline of a different sha does
    not satisfy this one.
    """
    latest_verdict: dict[str, str] = {}
    for ev in find_events(ledger_path, events.FLAKE_BASELINE):
        latest_verdict[ev["task_sha"]] = ev["verdict"]
    return latest_verdict.get(task_sha) == "clean"


def admit_task(
    manifest: CorpusManifest,
    ledger_path,
    ctx: EventContext,
    *,
    candidate_id: str,
    task_sha: str,
    baseline_ref: str,
) -> TaskEntry:
    """Admit a pending candidate into ``manifest`` iff both preconditions hold.

    Refuses loudly on either missing precondition; on success flips the task's
    status to ``admitted``, pins its ``baseline_ref``, and ledgers exactly one
    ``task_admitted`` event so the admission decision is chain-anchored, not only
    in mutable manifest JSON [CO-4]. The task must already exist in the manifest
    as a pending candidate (mining wrote it there).
    """
    # Admission's two preconditions are read from the ledger; verify the chain
    # first so a hand-forged ledger cannot manufacture them [CO-5/PL-6].
    assert_chain(ledger_path)
    task = manifest.task(candidate_id)
    if task is None:
        raise CorpusError(
            f"no candidate {candidate_id!r} in manifest {manifest.corpus_id!r}"
        )
    if task.sha != task_sha:
        raise CorpusError(
            f"candidate {candidate_id!r} sha {task.sha} != approved sha {task_sha}; "
            "admission binds to the reviewed version"
        )
    if not has_curation_approval(ledger_path, candidate_id, task_sha):
        raise CurationRequiredError(
            f"candidate {candidate_id!r} has no curation_approval event; a mined "
            "task requires human curation before admission [AC-4]"
        )
    if not has_clean_baseline(ledger_path, task_sha):
        raise BaselinePrerequisiteError(
            f"candidate {candidate_id!r} has no clean flake baseline for sha "
            f"{task_sha}; a clean baseline is an admission prerequisite [AC-4]"
        )
    task.status = "admitted"
    task.baseline_ref = baseline_ref
    events.record_task_admitted(
        ledger_path, ctx, candidate_id=candidate_id, task_sha=task_sha,
        baseline_ref=baseline_ref,
    )
    return task


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
_PROP_SHA = "s" * 64


def _prepare_admit(ctx_dir: str) -> None:
    from pathlib import Path

    d = Path(ctx_dir)
    led = d / "ledger.ndjson"
    ctx = EventContext(experiment_id="prop")
    events.record_curation_approval(led, ctx, candidate_id="cand-prop",
                                    task_sha=_PROP_SHA, approver="curator")
    events.record_flake_baseline(led, ctx, task_id="cand-prop", task_sha=_PROP_SHA, k=5,
                                 results=[{"run": i, "passed": True} for i in range(5)],
                                 verdict="clean")


def _admit_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    d = Path(ctx_dir)
    manifest = CorpusManifest(
        corpus_id="internal-prop", semver="1.0.0", kind="internal",
        boundary_path="/tmp/prop-boundary",
        tasks=[TaskEntry(task_id="cand-prop", sha=_PROP_SHA, status="pending-curation")],
    )
    admit_task(manifest, d / "ledger.ndjson", EventContext(experiment_id="prop"),
               candidate_id="cand-prop", task_sha=_PROP_SHA, baseline_ref="b1")


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("corpus-admit", _admit_entrypoint, prepare=_prepare_admit)


_register()
