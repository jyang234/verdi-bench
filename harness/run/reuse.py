"""Control-run reuse: bundle export/import [control-reuse plan].

A reused control is mediated by a self-contained **bundle** exported from a
completed source experiment while its trial workspaces are still alive
(ephemeral containers mean they will not survive to the next session). The
bundle carries, per control-arm ``(task_id, repetition)`` cell, the source trial
record, its grade, and the *bounded judged-diff snapshot* (so the judge can
compare the reused control against a fresh contender later), plus the control
fingerprint and provenance of the source run.

This module builds and verifies bundles and (slice 4) imports them into a target
ledger under the ``reused_*`` event kinds. No LLM client is imported — the diff
snapshot reuses the judge's own read-only assembler, which touches no model.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..corpus.commit import load_task_dicts
from ..corpus.public import content_sha
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import find_events, ledger_head_hash
from ..plan.lock import assert_lock
from ..schema.experiment import Arm
from ..version import instrument_identity
from .control_reuse import (
    ControlReuseError,
    assert_fingerprint_match,
    compute_fingerprint,
    primary_pair_contender,
)
from .settings import load_run_settings

BUNDLE_VERSION = 1

# Judged-diff snapshots are stashed beside the ledger at import (large; the chain
# carries only their sha, the trajectory_sha precedent). The reused judge
# assembler reads them from here.
REUSED_DIFF_SUBDIR = "reused_control/diffs"

# Grade-event envelope keys that are ledger transport, not grade content — a
# reused bundle carries the grade payload, not the source chain's provenance.
_ENVELOPE_KEYS = ("event", "provenance", "prev_hash", "hash")


class ControlBundleError(ControlReuseError):
    """A control bundle could not be built or is malformed/inconsistent."""


def _uniform(values: list, what: str):
    """The single shared value across a control arm's trials, or a loud refusal.

    A control run that mixed engines or instrument versions across its trials is
    not a coherent baseline to reuse — refuse to export rather than pick one."""
    distinct = sorted({v for v in values if v is not None})
    if len(distinct) > 1:
        raise ControlBundleError(
            f"control trials disagree on {what} ({distinct}); a mixed-{what} "
            "control run is not a coherent baseline to reuse"
        )
    return distinct[0] if distinct else None


def _grade_payload(grade_event: dict) -> dict:
    """The grade content stripped of ledger-envelope keys."""
    return {k: v for k, v in grade_event.items() if k not in _ENVELOPE_KEYS}


def _control_fingerprint(
    *, arm: Arm, task_dicts: list[dict], experiment_dir: Path, engine: str,
    settings, spec, instrument_git_sha: str,
) -> dict:
    proxy_allowlist = settings.proxy.allowlist if settings.proxy is not None else []
    plugin_ids = sorted({p for t in task_dicts for p in (t.get("plugins") or [])})
    return compute_fingerprint(
        arm=arm,
        task_dicts=task_dicts,
        experiment_dir=experiment_dir,
        engine=engine,
        quotas=settings.quotas,
        proxy_allowlist=proxy_allowlist,
        infra_hosts=sorted(spec.infra_hosts),
        repetitions=spec.repetitions,
        plugin_ids=plugin_ids,
        instrument_git_sha=instrument_git_sha,
    )


def build_bundle(source_experiment_dir, control_arm: str) -> dict:
    """Build a reuse bundle for ``control_arm`` from a completed source run.

    Verifies the source lock + chain, snapshots each control trial's judged diff
    while the workspace is still readable, and stamps the control fingerprint so
    a later import can prove the control is unchanged. Returns the bundle payload
    (its ``bundle_sha256`` is a hash over everything else, for tamper-evidence)."""
    from ..judge.assemble import read_workspace_diff  # local: keeps run import-light

    src = Path(source_experiment_dir)
    lock = assert_lock(src / "experiment.yaml", src / "ledger.ndjson")
    spec = lock.spec
    ledger = src / "ledger.ndjson"

    arm = next((a for a in spec.arms if a.name == control_arm), None)
    if arm is None:
        raise ControlBundleError(
            f"arm {control_arm!r} is not declared in {src / 'experiment.yaml'}; "
            f"declared arms: {[a.name for a in spec.arms]}"
        )

    trial_events = [
        ev for ev in find_events(ledger, events.TRIAL)
        if ev["trial_record"]["arm"] == control_arm
    ]
    if not trial_events:
        raise ControlBundleError(
            f"no trials for control arm {control_arm!r} in {ledger}; nothing to export"
        )
    grades = {g["trial_id"]: _grade_payload(g) for g in find_events(ledger, events.GRADE)}

    engine = _uniform([e["trial_record"]["provenance"].get("engine") for e in trial_events], "engine")
    instrument_git_sha = _uniform(
        [e["provenance"]["instrument"]["git_sha"] for e in trial_events], "instrument version"
    )
    image_digests = sorted(
        {e["trial_record"]["provenance"].get("image_digest") for e in trial_events} - {None}
    )
    harbor_versions = sorted(
        {e["trial_record"]["provenance"].get("harbor_version") for e in trial_events} - {None}
    )

    task_dicts = load_task_dicts(src)
    settings = load_run_settings(src, spec=spec)
    fingerprint = _control_fingerprint(
        arm=arm, task_dicts=task_dicts, experiment_dir=src, engine=engine,
        settings=settings, spec=spec, instrument_git_sha=instrument_git_sha,
    )

    cells = []
    for ev in trial_events:
        tr = ev["trial_record"]
        cells.append(
            {
                "task_id": tr["task_id"],
                "repetition": tr["repetition"],
                "trial_record": tr,
                "grade": grades.get(tr["trial_id"]),
                "diff": read_workspace_diff(tr.get("artifacts_path")),
            }
        )

    payload = {
        "bundle_version": BUNDLE_VERSION,
        "source_experiment_id": src.name,
        "source_ledger_head_hash": ledger_head_hash(ledger),
        "control_arm": control_arm,
        "fingerprint": fingerprint,
        "audit": {
            "engine": engine,
            "instrument_git_sha": instrument_git_sha,
            "image_digests": image_digests,
            "harbor_versions": harbor_versions,
        },
        "cells": sorted(cells, key=lambda c: (c["task_id"], c["repetition"])),
    }
    payload["bundle_sha256"] = content_sha(payload)
    return payload


def write_bundle(payload: dict, path) -> Path:
    """Persist a bundle as canonical JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_bundle(path) -> dict:
    """Read a bundle file (its self-sha is checked by :func:`verify_bundle`)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bundle_sha(payload: dict) -> str:
    """Recompute a bundle's self sha over everything but the sha field."""
    return content_sha({k: v for k, v in payload.items() if k != "bundle_sha256"})


def verify_bundle(payload: dict) -> None:
    """Refuse a tampered or malformed bundle before it is imported."""
    recorded = payload.get("bundle_sha256")
    if recorded is None:
        raise ControlBundleError("bundle carries no bundle_sha256")
    if bundle_sha(payload) != recorded:
        raise ControlBundleError(
            "bundle_sha256 does not match its contents — the bundle was modified "
            "after export; refusing to import"
        )
    if payload.get("bundle_version") != BUNDLE_VERSION:
        raise ControlBundleError(
            f"bundle_version {payload.get('bundle_version')} != {BUNDLE_VERSION}; "
            "re-export with the current instrument"
        )


# --- import into a target experiment ----------------------------------------
def reused_diff_path(experiment_dir, trial_id: str) -> Path:
    """Where the judged-diff snapshot for a reused control trial is stashed —
    shared by import (write) and the reused judge assembler (read)."""
    return Path(experiment_dir) / REUSED_DIFF_SUBDIR / f"{trial_id}.txt"


def current_fingerprint(
    experiment_dir, spec, control_arm: str, *, engine: str, settings
) -> dict:
    """The control fingerprint for THIS experiment's ``control_arm``, computed
    from the current spec / tasks / holdouts / run.config + instrument version —
    the side compared against a bundle's recorded fingerprint at preflight."""
    arm = next((a for a in spec.arms if a.name == control_arm), None)
    if arm is None:
        raise ControlBundleError(
            f"reuse names control arm {control_arm!r}, absent from this experiment's "
            f"arms {[a.name for a in spec.arms]}"
        )
    return _control_fingerprint(
        arm=arm,
        task_dicts=load_task_dicts(experiment_dir),
        experiment_dir=Path(experiment_dir),
        engine=engine,
        settings=settings,
        spec=spec,
        instrument_git_sha=instrument_identity()["git_sha"],
    )


def already_imported(ledger_path, bundle_sha256: str) -> bool:
    """Whether this bundle was already imported into the target ledger — makes a
    ``bench run`` resume re-invocation idempotent instead of double-importing."""
    return any(
        ev["bundle_sha256"] == bundle_sha256
        for ev in find_events(ledger_path, events.CONTROL_REUSED)
    )


def import_bundle(
    experiment_dir,
    bundle: dict,
    ctx: EventContext,
    *,
    engine: str,
    spec,
    settings,
) -> str:
    """Preflight-gate and import a control bundle into this experiment's ledger.

    Verifies the bundle self-sha, then refuses loudly unless the current control
    fingerprint matches the bundle's byte-for-byte (:func:`assert_fingerprint_match`
    names any drift). On a match, appends the ``control_reused`` summary and, per
    cell, a ``reused_trial`` + ``reused_grade``, stashing each judged-diff snapshot
    beside the ledger. Idempotent across resume. Returns the reused control arm
    name so the scheduler can drop its cells."""
    experiment_dir = Path(experiment_dir)
    ledger = experiment_dir / "ledger.ndjson"
    verify_bundle(bundle)
    control_arm = bundle["control_arm"]

    # Idempotency FIRST: a completed import is marked by its control_reused event
    # (appended last, below). Returning here before the fingerprint gate makes a
    # resume robust — re-asserting the gate over data already immutably on the
    # chain is redundant and would spuriously refuse after any gated drift (e.g.
    # an instrument git-sha bump moves the grader component).
    if already_imported(ledger, bundle["bundle_sha256"]):
        return control_arm

    # v1 reuses only a control that is one of the pre-registered primary pair
    # (arms[0]/arms[1]); a >2-arm control has no defined contender. Refuse loudly
    # at import rather than silently degrade downstream (empty judgment / half
    # exploratory section).
    if primary_pair_contender(spec, control_arm) is None:
        raise ControlBundleError(
            f"control arm {control_arm!r} is not in the pre-registered primary pair "
            f"({[spec.arms[0].name, spec.arms[1].name]}); reuse of a non-primary-pair "
            "control is not supported [v1]"
        )

    current = current_fingerprint(
        experiment_dir, spec, control_arm, engine=engine, settings=settings
    )
    assert_fingerprint_match(current, bundle["fingerprint"])

    reused_from = {
        "source_experiment_id": bundle["source_experiment_id"],
        "bundle_sha256": bundle["bundle_sha256"],
    }
    # Per-cell idempotency: a partial import (killed mid-loop, before the
    # control_reused marker) resumes by writing only the cells not yet on the
    # chain — never a duplicate reused_trial.
    already_cells = {
        e["trial_record"]["trial_id"] for e in find_events(ledger, events.REUSED_TRIAL)
    }
    for cell in bundle["cells"]:
        tr = cell["trial_record"]
        if tr["trial_id"] in already_cells:
            continue
        diff = cell.get("diff") or ""
        path = reused_diff_path(experiment_dir, tr["trial_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(diff, encoding="utf-8")
        events.record_reused_trial(
            ledger, ctx, trial_record=tr, reused_from=reused_from,
            diff_sha256=content_sha(diff),
        )
        if cell.get("grade") is not None:
            events.record_reused_grade(
                ledger, ctx, grade=cell["grade"], reused_from=reused_from
            )
    # LAST: the control_reused summary is the completion marker. Appending it only
    # after every cell is written means already_imported() is true iff the whole
    # import finished — a crash mid-loop leaves it absent and the resume above
    # completes the missing cells instead of attesting a partial import as done.
    events.record_control_reused(
        ledger,
        ctx,
        source_experiment_id=bundle["source_experiment_id"],
        source_ledger_head_hash=bundle["source_ledger_head_hash"],
        bundle_sha256=bundle["bundle_sha256"],
        fingerprint=bundle["fingerprint"],
        control_arm=control_arm,
        cells=[{"task_id": c["task_id"], "repetition": c["repetition"]} for c in bundle["cells"]],
    )
    return control_arm


def reused_arms(ledger_path) -> set[str]:
    """Every control arm imported as a reused control on this ledger.

    The scheduler drops these arms on EVERY run — not just the invocation that
    passed ``--reuse-control`` — so a resume that omits the flag cannot silently
    run the control arm fresh (which would let the official paired path compare
    non-interleaved arms)."""
    return {ev["control_arm"] for ev in find_events(ledger_path, events.CONTROL_REUSED)}


# --- one-event property registration ----------------------------------------
def _reused_entrypoint(kind: str):
    def fn(ctx_dir: str) -> None:
        d = Path(ctx_dir)
        ledger = d / "ledger.ndjson"
        ctx = EventContext(experiment_id="prop")
        reused_from = {"source_experiment_id": "src", "bundle_sha256": "sha"}
        if kind == "control-reused":
            events.record_control_reused(
                ledger, ctx, source_experiment_id="src", source_ledger_head_hash="h",
                bundle_sha256="sha", fingerprint={"digest": "d"}, control_arm="control",
                cells=[{"task_id": "t", "repetition": 0}],
            )
        elif kind == "reused-trial":
            events.record_reused_trial(
                ledger, ctx,
                trial_record={"trial_id": "tr", "task_id": "t", "arm": "control", "repetition": 0},
                reused_from=reused_from,
            )
        elif kind == "reused-grade":
            events.record_reused_grade(
                ledger, ctx,
                grade={"trial_id": "tr", "task_sha": "s", "assertions": [], "binary_score": True},
                reused_from=reused_from,
            )
    return fn


def _register() -> None:
    from ..entrypoints import register_entrypoint

    for kind in ("control-reused", "reused-trial", "reused-grade"):
        register_entrypoint(kind, _reused_entrypoint(kind))


_register()
