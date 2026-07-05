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

from pathlib import Path
from typing import Optional

from ..corpus.commit import load_task_dicts
from ..corpus.public import content_sha
from ..ledger import events
from ..ledger.query import find_events, ledger_head_hash
from ..plan.lock import assert_lock
from ..schema.experiment import Arm
from .control_reuse import ControlReuseError, compute_fingerprint
from .settings import load_run_settings

BUNDLE_VERSION = 1

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
