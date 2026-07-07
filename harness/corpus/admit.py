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

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..contamination.canary import derive_canary, embed_canary, hash_canary
from ..errors import VerdiRefusal
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, find_events
from .attestation import verify_approval
from .registry import CorpusError, CorpusManifest, TaskEntry


class CurationRequiredError(CorpusError):
    """No curation_approval event for the candidate [AC-4]."""


class BaselinePrerequisiteError(CorpusError):
    """Approved but no clean flake baseline for the task sha [AC-4]."""


class AttestationError(CorpusError):
    """A curation approval's signature does not verify [D-P4-3]."""


class UnauthorizedCuratorError(CorpusError):
    """The approval's signer is not in the authorized-curator keyring [D-P4-3]."""


class SelfApprovalError(CorpusError):
    """The approver is the task's miner — self-approval is barred [CO-7, D-P4-3]."""


def curation_approval_for(ledger_path, candidate_id: str, task_sha: str):
    """The curation_approval event for ``(candidate_id, task_sha)`` (latest wins),
    or None — carries the signature admission verifies."""
    found = None
    for ev in find_events(ledger_path, events.CURATION_APPROVAL):
        if ev["candidate_id"] == candidate_id and ev["task_sha"] == task_sha:
            found = ev
    return found


def has_curation_approval(ledger_path, candidate_id: str, task_sha: str) -> bool:
    return curation_approval_for(ledger_path, candidate_id, task_sha) is not None


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
    keyring: dict,
    candidate_content: Optional[dict] = None,
) -> TaskEntry:
    """Admit a pending candidate into ``manifest`` iff every precondition holds.

    Refuses loudly on any missing precondition; on success flips the task's status
    to ``admitted``, pins its ``baseline_ref``, and ledgers exactly one
    ``task_admitted`` event so the admission decision is chain-anchored, not only
    in mutable manifest JSON [CO-4]. Preconditions: a curation approval whose
    signature verifies (``AttestationError`` otherwise), whose signer is in the
    authorized-curator ``keyring`` (``UnauthorizedCuratorError``), and whose
    approver is not the task's miner (``SelfApprovalError``) [D-P4-3]; plus a clean
    flake baseline. The task must already exist as a pending candidate.

    ``candidate_content`` is the task's stored content [EVAL-10 AC-2]: when
    given, the canary embed is *validated before any mutation or ledger write*
    (a failing embed refuses admission outright — never a ledgered-but-unmarked
    tear) and the manifest entry records ``sha256(canary)``. Without content no
    canary hash is recorded: claiming a canary that was never embedded would
    turn the probe's honest ``unprobed`` into a false ``negative``.
    """
    # Admission's preconditions are read from the ledger; verify the chain first
    # so a hand-forged ledger cannot manufacture them [CO-5/PL-6].
    assert_chain(ledger_path)
    task = manifest.task(candidate_id)
    if task is None:
        raise CorpusError(
            f"no candidate {candidate_id!r} in manifest {manifest.corpus_id!r}"
        )
    # PRA-M11: refuse an already-admitted candidate. Without this, a re-run (e.g.
    # after a torn late-save) appended a SECOND task_admitted event — one attempted
    # operation must be one admission, not an unbounded re-ledgering.
    if task.status == "admitted":
        raise CorpusError(
            f"candidate {candidate_id!r} is already admitted; re-admission is "
            "refused [PRA-M11]"
        )
    if task.sha != task_sha:
        raise CorpusError(
            f"candidate {candidate_id!r} sha {task.sha} != approved sha {task_sha}; "
            "admission binds to the reviewed version"
        )
    # PRA-M11: verify the SUPPLIED candidate content actually hashes to the
    # approved task_sha, so a stale/tampered candidate file cannot be admitted —
    # and its canary embedded into unreviewed bytes — while the manifest entry's
    # sha still matches. Uses the one canonical content-hash primitive (the same
    # one Candidate.content_sha and the flake baseline bind to).
    if candidate_content is not None:
        from .public import content_sha

        computed = content_sha(
            {
                "workspace_ref": candidate_content.get("workspace_ref"),
                "prompt": candidate_content.get("prompt"),
                "holdouts": candidate_content.get("holdouts", []),
                "groundwork_rules": candidate_content.get("groundwork_rules"),
            }
        )
        if computed != task_sha:
            raise CorpusError(
                f"candidate content sha {computed} != approved task_sha {task_sha}; "
                "the supplied file is not the reviewed version — refusing to admit "
                "and canary-embed unreviewed bytes [PRA-M11]"
            )
    approval = curation_approval_for(ledger_path, candidate_id, task_sha)
    if approval is None:
        raise CurationRequiredError(
            f"candidate {candidate_id!r} has no curation_approval event; a mined "
            "task requires human curation before admission [AC-4]"
        )
    # D-P7-3: identity-bound authorization. Resolve the *named* approver in the
    # keyring and verify the signature against THAT approver's registered key —
    # not the self-attested signer_public_key. Verifying against the self-attested
    # key let any authorized-key holder self-approve by relabeling the approver;
    # binding to the named approver's key refuses that (a relabeled approval no
    # longer verifies under the impersonated approver's key).
    approver = approval["approver"]
    if approver not in keyring:
        raise UnauthorizedCuratorError(
            f"curation approval for {candidate_id!r} names approver {approver!r}, "
            "who is not in the authorized-curator keyring; only registered "
            "approver identities can admit a task [D-P7-3]"
        )
    authorized_key = keyring[approver]
    if not verify_approval(
        approval.get("signature", ""), authorized_key,
        candidate_id=candidate_id, task_sha=task_sha, approver=approver,
    ):
        raise AttestationError(
            f"curation approval for {candidate_id!r} does not verify against the "
            f"registered key for approver {approver!r}; a signature under any "
            "other key (including a relabeled self-approval) is refused [D-P7-3]"
        )
    # Defense in depth: the self-attested signer key must equal the keyring key,
    # so a mismatched attestation is a loud refusal rather than silently ignored.
    if approval.get("signer_public_key") != authorized_key:
        raise UnauthorizedCuratorError(
            f"curation approval for {candidate_id!r} attests a signer key that "
            f"does not match the keyring key for approver {approver!r} [D-P7-3]"
        )
    # The approver≠miner bar can only be enforced if the miner is recorded; a
    # candidate with no miner cannot be verified, so admission is refused rather
    # than silently skipping the bar (which would let a miner self-approve any
    # task whose miner id was never recorded) [CO-7, D-P4-3, fail-closed].
    if task.miner is None:
        raise SelfApprovalError(
            f"candidate {candidate_id!r} has no recorded miner; the approver≠miner "
            "bar cannot be verified, so admission is refused [CO-7]"
        )
    if approval["approver"] == task.miner:
        raise SelfApprovalError(
            f"approver {approval['approver']!r} is the miner of {candidate_id!r}; "
            "the miner cannot approve their own task [CO-7]"
        )
    if not has_clean_baseline(ledger_path, task_sha):
        raise BaselinePrerequisiteError(
            f"candidate {candidate_id!r} has no clean flake baseline for sha "
            f"{task_sha}; a clean baseline is an admission prerequisite [AC-4]"
        )
    # EVAL-10 AC-2: derive + validate the embed BEFORE any mutation or ledger
    # write, so an embed failure (no prompt, double embed) refuses admission
    # with nothing torn. The hash is recorded only when content was actually
    # embedded; the value never enters the manifest (re-derivable from
    # task_sha wherever the content is materialized).
    canary_sha256 = None
    if candidate_content is not None:
        canary = derive_canary(task_sha)
        embed_canary(candidate_content, canary)  # pure; raises CanaryError pre-event
        canary_sha256 = hash_canary(canary)
    task.status = "admitted"
    task.baseline_ref = baseline_ref
    task.canary_sha256 = canary_sha256
    events.record_task_admitted(
        ledger_path, ctx, candidate_id=candidate_id, task_sha=task_sha,
        baseline_ref=baseline_ref,
    )
    return task


# --- two-phase admission persistence [refactor 07 §3; PRA-M11, EVAL-10 AC-2] --
class AdmitInputError(VerdiRefusal, RuntimeError):
    """The stored candidate content for admission is missing/malformed/inside
    the instrument repo — refused before ledgering [EVAL-10 AC-2]."""


class AdmitDestinationError(VerdiRefusal, RuntimeError):
    """An admission write destination is not writable — refused before
    ledgering, so nothing is torn [PRA-M11]."""


@dataclass(frozen=True)
class AdmitOutcome:
    """The result of a two-phase admission [PRA-M11]: the persisted embedded-copy
    path (when a candidate was embedded), and a post-ledger ``persist_error`` when
    the admission is on the chain but the manifest/embedded copy could not be
    written — surfaced with the recovery hint, never swallowed."""

    embedded_path: Path | None = None
    persist_error: str | None = None


def admit_with_persistence(
    manifest: CorpusManifest,
    ledger_path,
    ctx: EventContext,
    *,
    candidate_id: str,
    task_sha: str,
    baseline_ref: str,
    keyring: dict,
    manifest_path,
    candidate_content: Optional[dict] = None,
    candidate_json=None,
) -> AdmitOutcome:
    """The admission's two-phase write orchestration [PRA-M11, EVAL-10 AC-2].

    Phase 1 (pre-ledger, fail-closed): probe every write destination — a
    non-writable manifest/embedded-copy path refuses with NOTHING ledgered.
    Phase 2 (post-ledger): :func:`admit_task` ledgers exactly one
    ``task_admitted`` (validating the canary embed first), then the embedded copy
    is persisted ALONGSIDE the reviewed file (never over it) and the manifest is
    saved. A failure there is reported as ``AdmitOutcome.persist_error`` — the
    admission is on the chain, re-save to reconcile — never swallowed.

    Sits beside :func:`admit_task` so the api/CLI keep argument handling +
    refusal mapping only [refactor 07 §3]. ``candidate_content`` is the
    already-read stored content (the api validates its provenance before
    ledgering, raising ``AdmitInputError`` there)."""
    # PRA-M11: validate the write destinations BEFORE ledgering, so a non-writable
    # manifest/embedded-copy path fails closed with nothing torn.
    for dest in (manifest_path, candidate_json):
        if dest is not None and not os.access(dest.parent, os.W_OK):
            raise AdmitDestinationError(
                f"admission destination {dest.parent} is not writable; refusing "
                "before ledgering [PRA-M11]"
            )
    # admit_task validates the canary embed BEFORE ledgering, so an embed refusal
    # (no prompt, double embed) leaves nothing torn.
    admit_task(
        manifest, ledger_path, ctx, candidate_id=candidate_id, task_sha=task_sha,
        baseline_ref=baseline_ref, keyring=keyring, candidate_content=candidate_content,
    )
    # EVAL-10 AC-2: persist the embedded copy ALONGSIDE the reviewed file — never
    # over it. embed_canary is pure, so this repeats the exact call admit_task
    # already validated. A failure here (post-ledger) is reported loudly with the
    # recovery hint, not swallowed [PRA-M11].
    embedded_path: Path | None = None
    try:
        if candidate_content is not None:
            embedded = embed_canary(candidate_content, derive_canary(task_sha))
            ep = candidate_json.with_suffix(".embedded.json")
            ep.write_text(
                json.dumps(embedded, sort_keys=True, indent=2), encoding="utf-8"
            )
            embedded_path = ep
        manifest.save(manifest_path)
    except OSError as e:
        return AdmitOutcome(
            embedded_path=embedded_path,
            persist_error=(
                f"task_admitted was ledgered but persisting the manifest/embedded "
                f"copy failed: {e}. The admission is on the chain; re-save the "
                f"manifest to {manifest_path} to reconcile [PRA-M11]"
            ),
        )
    return AdmitOutcome(embedded_path=embedded_path)


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
_PROP_SHA = "s" * 64
# A fixed test curator keypair (deterministic — key generation is out of band).
_CURATOR_PRIV = "57d8af6bd26b16f1f558e600e70fb2a40a5349804c864b3513b12015dc155556"
_CURATOR_PUB = "54f22d27057d6c0a336de3f2d0df143546f31591c169072e90f18f651e49e148"


def _prepare_admit(ctx_dir: str) -> None:
    from pathlib import Path

    from .attestation import sign_approval

    d = Path(ctx_dir)
    led = d / "ledger.ndjson"
    ctx = EventContext(experiment_id="prop")
    sig, pk = sign_approval(_CURATOR_PRIV, candidate_id="cand-prop",
                            task_sha=_PROP_SHA, approver="curator")
    events.record_curation_approval(led, ctx, candidate_id="cand-prop",
                                    task_sha=_PROP_SHA, approver="curator",
                                    signature=sig, signer_public_key=pk)
    events.record_flake_baseline(led, ctx, task_id="cand-prop", task_sha=_PROP_SHA, k=5,
                                 results=[{"run": i, "passed": True} for i in range(5)],
                                 verdict="clean")


def _admit_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from .._entrypoint_fixtures import prop_admit_manifest

    d = Path(ctx_dir)
    manifest = prop_admit_manifest(_PROP_SHA)
    admit_task(manifest, d / "ledger.ndjson", EventContext(experiment_id="prop"),
               candidate_id="cand-prop", task_sha=_PROP_SHA, baseline_ref="b1",
               keyring={"curator": _CURATOR_PUB})


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("corpus-admit", _admit_entrypoint, prepare=_prepare_admit)


_register()
