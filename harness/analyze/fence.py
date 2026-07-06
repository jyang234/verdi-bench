"""Itemized official-fence report [EVAL-14 AC-6, AC-7].

``render_markdown`` enforces the official fence fail-fast — the first unmet
requirement raises. An observer needs the opposite projection: every
requirement named with its own state, so "why can't I render official?"
answers itself on a screen. This module re-evaluates the SAME checks
``_assert_official_calibration`` performs (same helpers, same vocabulary —
imported, not re-implemented) and reports them as items instead of raising.

Read-only and side-effect-free: no event is appended (``cant_analyze``
emission lives in the analyze CLI, not here), nothing is rendered. States:
``ok`` | ``failed`` | ``unchecked`` — a check that *requires* the corpus
manifest is ``unchecked`` (with the reason) when none is supplied, and the
fence is then not passable, exactly as the render would refuse
[EVAL-8 AC-2]. Two render-scoped validations (findings provenance and the
selfcheck↔deployed CI-method agreement) are re-run per render and are out of
scope for a ledger-level checklist; the selfcheck item names the validated
method so the render-time agreement is inspectable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from ..contamination.summary import latest_probe, probe_asymmetries
from ..ledger import events
from ..ledger.query import find_events, verify
from ..schema.errors import SpecError
from ..schema.experiment import ExperimentSpec
from .report import (
    CorrectionMismatchError,
    _assert_correction_consistent,
    _judge_summary,
    _ledgered_calibration_status,
    _task_ids_run,
    effective_multi_arm_correction,
)
from .selfcheck import latest_selfcheck, selfcheck_status


def _item(item_id: str, name: str, state: str, detail: str) -> dict:
    return {"id": item_id, "name": name, "state": state, "detail": detail}


def official_fence_report(experiment_dir, *, corpus_manifest=None) -> dict:
    """Every official-fence requirement with its current state.

    Returns ``{official_ready, items}``; ``official_ready`` is true only when
    every item is ``ok`` — an ``unchecked`` item (no manifest supplied) blocks
    readiness exactly as the render's refusal would.
    """
    experiment_dir = Path(experiment_dir)
    ledger_path = experiment_dir / "ledger.ndjson"
    items: list[dict] = []

    # 1. chain integrity — nothing downstream is trustworthy without it.
    chain = verify(ledger_path)
    items.append(
        _item(
            "chain",
            "hash chain verifies",
            "ok" if chain.ok else "failed",
            "" if chain.ok else (chain.detail or "chain verification failed"),
        )
    )
    if not chain.ok:
        # Fail closed: the remaining checks would read unverified content.
        return {"official_ready": False, "items": items}

    locks = find_events(ledger_path, events.EXPERIMENT_LOCKED)
    lock = locks[0] if locks else None
    spec_corpus: Optional[dict] = None
    try:
        spec = ExperimentSpec.from_yaml(experiment_dir / "experiment.yaml")
        spec_corpus = {"id": spec.corpus.id, "version": spec.corpus.version}
    except (SpecError, yaml.YAMLError, OSError):
        # An unreadable/absent spec is a describable state for an observer —
        # reported as the named failed item below, never smoothed over.
        spec = None

    items.append(
        _item(
            "lock",
            "experiment locked",
            "ok" if lock else "failed",
            "" if lock else "no experiment_locked event; run `bench plan` first",
        )
    )
    if spec is None:
        items.append(
            _item("corpus_identity", "pre-registered corpus cited", "failed",
                  "experiment.yaml missing or unreadable")
        )

    # 2–3. corpus identity + coverage: require the manifest [EVAL-8 AC-2].
    if spec_corpus is not None:
        if corpus_manifest is None:
            detail = (
                f"official findings require the full-run-validated corpus manifest "
                f"for {spec_corpus['id']}@{spec_corpus['version']}; none supplied"
            )
            items.append(_item("corpus_identity", "pre-registered corpus cited",
                               "unchecked", detail))
            items.append(_item("corpus_coverage", "every task run is admitted",
                               "unchecked", detail))
        else:
            identity_ok = (
                corpus_manifest.corpus_id == spec_corpus["id"]
                and corpus_manifest.semver == spec_corpus["version"]
            )
            items.append(
                _item(
                    "corpus_identity",
                    "pre-registered corpus cited",
                    "ok" if identity_ok else "failed",
                    ""
                    if identity_ok
                    else f"manifest is {corpus_manifest.corpus_id}@"
                    f"{corpus_manifest.semver}, pre-registered "
                    f"{spec_corpus['id']}@{spec_corpus['version']}",
                )
            )
            missing = sorted(
                t for t in _task_ids_run(ledger_path)
                if not corpus_manifest.is_schedulable(t)
            )
            items.append(
                _item(
                    "corpus_coverage",
                    "every task run is admitted",
                    "ok" if not missing else "failed",
                    "" if not missing else f"tasks {missing} ran but are not admitted",
                )
            )

        # 4. ledgered calibration status [CO-4].
        cal = _ledgered_calibration_status(
            ledger_path, spec_corpus["id"], spec_corpus["version"]
        )
        items.append(
            _item(
                "calibration",
                "corpus full-run-validated (ledgered)",
                "ok" if cal == "full-run-validated" else "failed",
                "" if cal == "full-run-validated"
                else f"ledgered calibration status is {cal!r}",
            )
        )

    # 5. rubric commitment [D-P7-6]. A legacy lock (no committed hash) is a
    # disclosed caveat at render time, not a refusal — mirrored here as ok.
    locked_rubric = (lock or {}).get("rubric_sha256")
    if locked_rubric is None:
        items.append(_item("rubric", "rubric matches the lock", "ok",
                           "legacy lock: no rubric hash committed (render adds a caveat)"))
    else:
        disagreeing = sorted(
            s for s in _judge_summary(ledger_path)["rubric_shas"] if s != locked_rubric
        )
        items.append(
            _item(
                "rubric",
                "rubric matches the lock",
                "ok" if not disagreeing else "failed",
                "" if not disagreeing
                else f"verdict rubric hash(es) {disagreeing} disagree with the lock",
            )
        )

    # 6. selfcheck currency [EVAL-1-D008]; names the validated CI method so the
    # render-time deployed-method agreement is inspectable.
    sc = selfcheck_status(ledger_path)
    validated = (latest_selfcheck(ledger_path) or {}).get("selected_method")
    items.append(
        _item(
            "selfcheck",
            "coverage selfcheck current",
            "ok" if sc == "current" else "failed",
            f"validated CI method: {validated}" if sc == "current"
            else {"missing": "no selfcheck has been run",
                  "failed": "the selfcheck failed",
                  "stale": "data was appended after the last selfcheck"}.get(sc, sc),
        )
    )

    # 7. contamination asymmetry [EVAL-10 AC-5]: recomputed from the ledgered
    # probe, exactly as the fence does.
    asymmetric = probe_asymmetries(latest_probe(ledger_path))
    items.append(
        _item(
            "contamination",
            "no asymmetric flagged contamination",
            "ok" if not asymmetric else "failed",
            "" if not asymmetric else f"{len(asymmetric)} asymmetric flag(s)",
        )
    )

    # 8. insulation alarms [F-M-C3]: a holdout-leak breach on the latest probe
    # refuses the official render, exactly as the render fence does.
    alarms = (latest_probe(ledger_path) or {}).get("alarms") or []
    items.append(
        _item(
            "insulation",
            "no holdout-leak insulation alarms",
            "ok" if not alarms else "failed",
            "" if not alarms else f"{len(alarms)} insulation alarm(s)",
        )
    )

    # 9. multi-arm correction consistency [F-H7, refactor 01 §4 D8]: evaluated
    # through the render fence's own helper, so this projection cannot show
    # ready while `bench analyze --official` refuses CorrectionMismatchError
    # over a chain carrying a differently-corrected prior official render.
    if spec is None:
        items.append(_item("correction", "multi-arm correction consistent",
                           "unchecked", "experiment.yaml missing or unreadable"))
    else:
        try:
            _assert_correction_consistent(
                effective_multi_arm_correction(spec), ledger_path
            )
        except CorrectionMismatchError as e:
            items.append(_item("correction", "multi-arm correction consistent",
                               "failed", str(e)))
        else:
            items.append(_item("correction", "multi-arm correction consistent",
                               "ok", ""))

    return {
        "official_ready": all(i["state"] == "ok" for i in items),
        "items": items,
    }
