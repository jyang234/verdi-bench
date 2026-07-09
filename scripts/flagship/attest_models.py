#!/usr/bin/env python3
"""Per-trial model attestation over a completed experiment (verdi-go plan §6, D4).

A deterministic verifier for the arm-model-attribution defect the pilot exposed:
the trial request carried each arm's declared model (``req.model_id``), but the
agents never passed it to the pinned ``claude`` CLI, so the CLI ran its BUILT-IN
default for every arm regardless of the arm spec — silently invalidating the arm
comparison. The native ``--output-format json`` result the agents now persist as
``agent_log.json`` carries a ``modelUsage`` object whose KEYS are the model ids the
CLI actually used; this tool reads them back and checks each trial ran the model
its ARM declared.

For every ``trial`` event in the experiment's ``ledger.ndjson`` it resolves the
arm's declared model from the locked ``experiment.yaml``, strips the provider
prefix (the same derivation ``req.model_id`` uses to build ``--model``, so the
comparison is like-with-like), loads ``<artifacts_path>/agent_log.json``, and
classifies:

  * ``OK``            — the log is the native claude result and EVERY ``modelUsage``
                        key equals the arm's declared bare model id;
  * ``MISMATCH``      — a native log whose ``modelUsage`` names a different model. A
                        ``[1m]``-suffixed key (the context-beta variant, an
                        UNCONTROLLED variable) mismatches an unsuffixed declared id
                        by exact equality, unless the declared id carries the suffix;
  * ``NO-NATIVE-LOG`` — no native result to attest: a missing artifacts dir/log, an
                        unreadable/unparseable file, or a non-native (generic) log.

A missing artifacts dir or unreadable log is a LOUD per-trial ``NO-NATIVE-LOG`` line,
never a silent skip. Exit 0 iff every trial is ``OK``. Stdlib + the repo's own
schema loader only — no LLM client (this is a deterministic verifier).

    uv run python scripts/flagship/attest_models.py <experiment-dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# The native CLI result the agents persist verbatim, and the object whose keys are
# the model ids the run actually used (PROBE-VERIFIED shape, e.g.
# {"claude-opus-4-8[1m]": {...}}). Its presence is the native-vs-generic-log test.
AGENT_LOG_FILENAME = "agent_log.json"
MODEL_USAGE_KEY = "modelUsage"

OK = "OK"
MISMATCH = "MISMATCH"
NO_NATIVE_LOG = "NO-NATIVE-LOG"


@dataclass(frozen=True)
class AttestRow:
    trial_id: str
    arm: str
    task_id: str
    declared: str  # the arm's declared bare model id (provider prefix stripped)
    observed: str  # the modelUsage key(s), or a bracketed reason for NO-NATIVE-LOG
    status: str


def bare_model_id(declared: str) -> str:
    """The declared model id with its provider prefix stripped (``claude-…`` from
    ``anthropic/claude-…``) — the SAME derivation the trial agent's ``req.model_id``
    uses to build ``--model``, so this compares like with like against ``modelUsage``."""
    return declared.split("/", 1)[-1]


def classify(declared_bare_id: str, log_data) -> tuple[str, str]:
    """Pure classification of a parsed ``agent_log.json`` value against an arm's
    declared bare model id. Returns ``(status, observed)``.

    A non-dict value or one without a dict ``modelUsage`` is ``NO-NATIVE-LOG`` (not
    the native result shape). An empty ``modelUsage`` attests nothing → ``MISMATCH``
    (never a vacuous ``OK``). Otherwise every key must equal ``declared_bare_id`` by
    EXACT equality — a ``[1m]``-suffixed key differs from an unsuffixed declared id,
    which is the intended mismatch (the suffix is an uncontrolled context-beta
    variant)."""
    if not isinstance(log_data, dict):
        return (NO_NATIVE_LOG, "<not a JSON object>")
    usage = log_data.get(MODEL_USAGE_KEY)
    if not isinstance(usage, dict):
        return (NO_NATIVE_LOG, f"<no {MODEL_USAGE_KEY}>")
    keys = sorted(usage)
    if not keys:
        return (MISMATCH, f"<empty {MODEL_USAGE_KEY}>")
    observed = ",".join(keys)
    status = OK if all(k == declared_bare_id for k in keys) else MISMATCH
    return (status, observed)


def attest_artifacts(declared_bare_id: str, artifacts_path: str) -> tuple[str, str]:
    """Read ``<artifacts_path>/agent_log.json`` and classify it. A missing dir/file
    or an unreadable/unparseable log is a LOUD ``NO-NATIVE-LOG`` (never a skip)."""
    log_path = Path(artifacts_path) / AGENT_LOG_FILENAME
    if not log_path.is_file():
        return (NO_NATIVE_LOG, f"<missing {AGENT_LOG_FILENAME}>")
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return (NO_NATIVE_LOG, f"<unreadable: {type(e).__name__}>")
    return classify(declared_bare_id, data)


def arm_models(experiment_dir: Path) -> dict[str, str]:
    """``{arm name: declared model id}`` from the locked ``experiment.yaml`` (the
    pre-registered arm spec is the single source of each arm's declared model)."""
    from harness.schema.experiment import ExperimentSpec

    spec = ExperimentSpec.from_yaml(Path(experiment_dir) / "experiment.yaml")
    return {arm.name: arm.model for arm in spec.arms}


def iter_trial_records(ledger_path: Path) -> list[dict]:
    """Every ``trial`` event's ``trial_record`` from the ledger, in ledger order. A
    malformed ledger line raises (a corrupt ledger is a hard failure, not a skip)."""
    records: list[dict] = []
    for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)  # loud on corruption — never silently skipped
        if ev.get("event") == "trial":
            rec = ev.get("trial_record")
            if not isinstance(rec, dict):
                raise ValueError(f"trial event without a trial_record: {ev!r}"[:200])
            records.append(rec)
    return records


def attest_experiment(experiment_dir) -> list[AttestRow]:
    """One :class:`AttestRow` per ledgered trial, in deterministic ``(arm, task_id,
    trial_id)`` order. Raises on a structural fault (missing experiment.yaml/ledger,
    or a trial whose arm is not in the spec) — those are integrity failures, loud."""
    experiment_dir = Path(experiment_dir)
    models = arm_models(experiment_dir)
    records = iter_trial_records(experiment_dir / "ledger.ndjson")

    rows: list[AttestRow] = []
    for rec in records:
        arm = rec.get("arm")
        trial_id = rec.get("trial_id") or "<no trial_id>"
        task_id = rec.get("task_id") or "<no task_id>"
        artifacts_path = rec.get("artifacts_path")
        if arm not in models:
            raise ValueError(
                f"trial {trial_id!r} names arm {arm!r}, absent from experiment.yaml "
                f"arms {sorted(models)} — ledger/spec integrity failure"
            )
        declared_bare = bare_model_id(models[arm])
        if not artifacts_path:
            status, observed = (NO_NATIVE_LOG, "<no artifacts_path in trial_record>")
        else:
            status, observed = attest_artifacts(declared_bare, artifacts_path)
        rows.append(AttestRow(trial_id=trial_id, arm=arm, task_id=task_id,
                              declared=declared_bare, observed=observed, status=status))
    rows.sort(key=lambda r: (r.arm, r.task_id, r.trial_id))
    return rows


def render(rows: list[AttestRow]) -> str:
    """One aligned line per trial: ``trial-id  arm  declared  observed  status``."""
    header = ("trial-id", "arm", "declared", "observed", "status")
    table = [header] + [
        (r.trial_id, r.arm, r.declared, r.observed, r.status) for r in rows
    ]
    widths = [max(len(str(row[i])) for row in table) for i in range(len(header))]
    return "\n".join(
        "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(header))).rstrip()
        for row in table
    ) + "\n"


def summary_line(rows: list[AttestRow]) -> str:
    n_ok = sum(1 for r in rows if r.status == OK)
    n_mismatch = sum(1 for r in rows if r.status == MISMATCH)
    n_no_log = sum(1 for r in rows if r.status == NO_NATIVE_LOG)
    return (f"attested {len(rows)} trial(s): {n_ok} OK, {n_mismatch} MISMATCH, "
            f"{n_no_log} NO-NATIVE-LOG")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("experiment_dir", type=Path, help="a completed experiment directory")
    args = ap.parse_args(argv)
    try:
        rows = attest_experiment(args.experiment_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(rows))
    print(summary_line(rows))
    if not rows:
        print("ERROR: no trial events in the ledger — nothing to attest", file=sys.stderr)
        return 2
    return 0 if all(r.status == OK for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
