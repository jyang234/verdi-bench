#!/usr/bin/env python3
"""Tool-usage funnel metrics [verdi-go integration plan §6, exploratory tier].

Computes the three per-trial funnel metrics the plan's Track A4 pre-registers as
*watermarked (exploratory)* secondaries — the tuning telemetry for verdi-go's
development loop (Track B) — from a trial's ``artifacts/groundwork-mcp.jsonl``
(the real ``groundwork mcp --log`` transcript) crossed with its v3 trajectory
(``artifacts/trajectory.json``):

- ``grounded_before_edit``    — the agent grounded before it started editing;
- ``checked_after_last_edit`` — the agent re-checked after its last edit;
- ``verdict_heeded``          — no verdict the tool surfaced was shipped anyway.

This is a *standalone* utility: it imports NO harness code and parses the two
artifacts (and, in experiment mode, ``ledger.ndjson``) as plain JSON, so it runs
against a raw run tree and its core is trivially hermetic to test.

HONEST ORDERING LIMITATION — load-bearing; read before trusting the numbers.
The real ``--log`` (verdi-go ``cmd/groundwork/mcp.go`` ``logCall`` / ``newSession``)
writes ONE JSON line per tool call —
``{"call":{"name":..,"arguments":{..}},"service":..,"session":..[,"isError":true]}``
— plus ``{"init":true,"session":..}`` session boundaries. It is deterministic
*by omission*: **no timestamps and no sequence numbers** — its only order is
line/append order — and each line records the tool CALL, never the tool RESPONSE.
The v3 trajectory is a separately-captured, independently-ordered artifact, and
(verified against the ``claude_code`` adapter, which renders an MCP ``tools/call``
as a ``tool_call`` step with ``command=""`` and NO tool name) it does not preserve
MCP-call identity. The two artifacts therefore share NO clock or join key, so a
precise cross-source interleave of an individual MCP call against an individual
``file_edit`` step is NOT honestly computable — and this tool does not fabricate
one. Per the plan's telemetry-null discipline it computes only what each source
honestly supports:

* the MCP transcript's own (line-order) sequence of calls, and
* the trajectory's ``file_edit`` presence (the applicability gate),

so the two precedence metrics are read off the transcript's *intrinsic* order
(the first / last logged call), gated on the trajectory actually containing an
edit. ``verdict_heeded`` joins what the log surfaces with the grade-time gate
outcome (whether the trial *shipped* a violation, from the ledger).

Two readings, keyed on the ``--log`` format marker (the session-init line's
``"log":2``; **absence ⇒ v1**):

* **v1 (coarse).** The v1 log records the CALL, not the response, so it carries
  no rule ids: "surfaced" means "a verdict/card was produced" (a non-error
  ``fitness`` / ``ground`` call) and "shipped" is the merge-time gate outcome —
  ``verdict_heeded`` is then "surfaced and did not ship".
* **v2 (per-rule).** A v2 log's *successful* ``fitness`` line carries a
  structured ``result`` — ``{"violated":[<rule kind>|<from>|<to>, …],
  "cautions":N}`` (verdi-go ``cmd/groundwork/mcp.go`` ``fitnessVerdictLog`` /
  ``findingIdentity``). "surfaced" sharpens to "a ``fitness`` line surfaced ≥1
  VIOLATION" (``violated`` non-empty), and where the grade side exposes the
  BLOCKed rule kinds (the groundwork plugin's per-rule assertions carry the rule
  kind as their id — ``harness/grade/plugins/groundwork.py``), ``verdict_heeded``
  names WHICH surfaced identities overlapped a shipped rule
  (``verdict_heeded_overlap``). Matching is on rule KIND (the identity's first
  ``|``-field): the grade id is the kind, not the finding's from/to edge, so
  edge-level matching is best-effort and not attempted. A v2 log whose grade
  exposes no per-rule kinds falls back to the coarse "surfaced and did not ship".

An **absent** MCP log ⇒ every metric is ``null`` (*not applicable* — a
control/bare arm never had the surface), NEVER ``false``.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Optional

# Artifact names (plan §4 D7: the MCP log lands in /workspace/artifacts/).
MCP_LOG_FILENAME = "groundwork-mcp.jsonl"
TRAJECTORY_FILENAME = "trajectory.json"

# The pre-edit grounding tool, and the tools whose call surfaces a verdict/card
# to the agent (``ground`` returns the binding rules + reachable effects; ``fitness``
# returns the policy verdicts). Sourced from the live tool set in
# verdi-go cmd/groundwork/mcp.go ``toolDefs``.
GROUND_TOOL = "ground"
CHECK_TOOLS = frozenset({"fitness", "ground"})

# The three metric ids, in a fixed order (deterministic CSV/JSON columns).
METRIC_IDS = ("grounded_before_edit", "checked_after_last_edit", "verdict_heeded")

# The --log format marker (verdi-go cmd/groundwork/mcp.go logFormatVersion): the
# session-init line carries "log":2 in v2; its absence means v1. A v2 log's
# verdict-bearing calls carry a structured "result" the per-rule reading consumes.
_LOG_V2 = 2

# The grade assertion that carries the merge-time gate's top-line verdict
# (harness/grade/plugins/groundwork.py): source ``plugin:groundwork``, id
# ``groundwork:verdict``, result ``failed`` == BLOCK (a shipped violation).
_GW_PLUGIN_SOURCE = "plugin:groundwork"
_GW_VERDICT_ID = "groundwork:verdict"


class MCPCall:
    """One logged ``tools/call`` line from the real ``--log`` transcript.

    ``result`` is the v2 structured verdict object (``{"violated":[…],
    "cautions":N[,"truncated":true]}``) when the line carried one, else ``None`` —
    v1 lines and every non-verdict / errored call have no ``result``.
    """

    __slots__ = ("name", "arguments", "session", "service", "is_error", "result")

    def __init__(self, name: str, arguments, session, service, is_error: bool, result=None) -> None:
        self.name = name
        self.arguments = arguments
        self.session = session
        self.service = service
        self.is_error = is_error
        self.result = result


def parse_mcp_log(text: str) -> list[MCPCall]:
    """Parse the ``groundwork mcp --log`` JSONL into its ordered ``tools/call`` list.

    Order is LINE order — the log's only order (no timestamps/sequence in the real
    emission). ``{"init":...}`` session boundaries and any line that is not a JSON
    object with a ``call`` are skipped (robust to a partially-written tail, the
    metering-proxy-parse precedent). A ``call`` line's shape mirrors
    cmd/groundwork/mcp.go ``logCall``: ``call`` = the ``tools/call`` params
    (``{"name","arguments"}``), with ``service`` / ``session`` present when known
    and ``isError`` present only on a tool error.
    """
    calls: list[MCPCall] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # a partially-written / malformed tail line, not this trial's fault
        if not isinstance(obj, dict):
            continue
        call = obj.get("call")
        if not isinstance(call, dict):
            continue  # an {"init":true,...} boundary or a non-call line
        name = call.get("name")
        if not isinstance(name, str):
            continue
        result = obj.get("result")
        calls.append(
            MCPCall(
                name=name,
                arguments=call.get("arguments"),
                session=obj.get("session"),
                service=obj.get("service"),
                is_error=bool(obj.get("isError", False)),
                result=result if isinstance(result, dict) else None,
            )
        )
    return calls


def mcp_log_version(text: str) -> int:
    """The ``--log`` format version, read from the session-init line's ``"log"``
    marker (verdi-go ``cmd/groundwork/mcp.go``): 2 for log v2, 0 when absent (v1).

    Scans every line for the highest integer marker present — a log is v2 as soon
    as one init line declares it — and is robust to a partially-written / malformed
    tail (the ``parse_mcp_log`` precedent). ``bool`` is excluded explicitly: it is
    an ``int`` subclass in Python, and a stray ``"log":true`` must not read as v1.
    """
    version = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        marker = obj.get("log")
        if isinstance(marker, int) and not isinstance(marker, bool) and marker > version:
            version = marker
    return version


def _file_edits(trajectory: Optional[dict]) -> Optional[list[dict]]:
    """The trajectory's ``file_edit`` steps in order, or ``None`` if the trajectory
    is absent/unshaped (honest absence — distinct from an empty edit list)."""
    if not isinstance(trajectory, dict):
        return None
    steps = trajectory.get("steps")
    if not isinstance(steps, list):
        return None
    return [s for s in steps if isinstance(s, dict) and s.get("kind") == "file_edit"]


def compute_trial_metrics(
    mcp_text: Optional[str],
    trajectory: Optional[dict],
    shipped_violation: Optional[bool],
    shipped_rules: Optional[list[str]] = None,
) -> dict:
    """The pure funnel core: the three metrics + their context for one trial.

    ``mcp_text`` is the raw ``groundwork-mcp.jsonl`` bytes (``None`` == the log is
    ABSENT — a control/bare arm, not applicable). ``trajectory`` is the parsed v3
    trajectory dict (``None`` == absent). ``shipped_violation`` is the merge-time
    gate outcome (``True`` == the trial shipped a violation the gate BLOCKed;
    ``False`` == clean; ``None`` == unknown, e.g. an ungraded trial). Every metric
    is ``True`` / ``False`` / ``None``; ``None`` is explicit *not-applicable*,
    never laundered into ``False`` (telemetry-null discipline).

    ``shipped_rules`` (v2 only) is the list of rule KINDS the gate BLOCKed, from the
    grade's per-rule groundwork assertions (``_shipped_rules_from_grade``). When the
    log is v2 and it is available, ``verdict_heeded`` sharpens to the per-rule form
    and the row gains ``log_version`` / ``verdict_violations_surfaced`` /
    ``verdict_heeded_overlap``. A **v1 log returns the byte-identical v1 dict** — the
    v2 keys and this argument are ignored (a v1 log has no structured verdict).
    """
    # Absent MCP log ⇒ the surface was never wired for this arm: every metric is
    # not-applicable. This is the control-arm case and it is null, NOT false.
    if mcp_text is None:
        return {
            "grounded_before_edit": None,
            "checked_after_last_edit": None,
            "verdict_heeded": None,
            "has_mcp_log": False,
            "n_mcp_calls": None,
            "n_file_edits": (len(e) if (e := _file_edits(trajectory)) is not None else None),
            "verdict_surfaced": None,
        }

    calls = parse_mcp_log(mcp_text)
    edits = _file_edits(trajectory)
    # A verdict was SURFACED iff a fitness/ground call returned WITHOUT error (the
    # tool produced a verdict/card the agent saw). This coarse "a verdict was
    # surfaced" fact is the v1 signal and stays a stable column in v2 too.
    surfaced = any(c.name in CHECK_TOOLS and not c.is_error for c in calls)

    # Precedence metrics: gated on the trajectory actually containing an edit (no
    # edit ⇒ "before/after the edit" is not applicable ⇒ null), then read off the
    # transcript's own line order (its only honest order; see the module docstring).
    if edits is None or not edits:
        grounded_before_edit = None
        checked_after_last_edit = None
    else:
        grounded_before_edit = bool(calls) and calls[0].name == GROUND_TOOL
        checked_after_last_edit = bool(calls) and calls[-1].name in CHECK_TOOLS

    # The verdict signal: v1 coarse or v2 per-rule, keyed on the log-format marker.
    # A v1 log returns exactly {"verdict_heeded": …}; a v2 log adds the per-rule keys.
    verdict = _verdict_signal(
        mcp_log_version(mcp_text), calls, surfaced, shipped_violation, shipped_rules
    )

    return {
        "grounded_before_edit": grounded_before_edit,
        "checked_after_last_edit": checked_after_last_edit,
        "has_mcp_log": True,
        "n_mcp_calls": len(calls),
        "n_file_edits": (len(edits) if edits is not None else None),
        "verdict_surfaced": surfaced,
        **verdict,
    }


def _surfaced_identities(calls: list[MCPCall]) -> list[str]:
    """The DISTINCT, sorted structured violation identities a v2 log surfaced — the
    union of every successful call's ``result.violated`` (today only ``fitness``
    emits one). Empty when nothing surfaced or on a v1 log (no ``result``)."""
    ids: set[str] = set()
    for c in calls:
        if c.result is None or c.is_error:
            continue
        for v in c.result.get("violated") or []:
            if isinstance(v, str):
                ids.add(v)
    return sorted(ids)


def _verdict_signal(
    log_version: int,
    calls: list[MCPCall],
    surfaced: bool,
    shipped_violation: Optional[bool],
    shipped_rules: Optional[list[str]],
) -> dict:
    """``verdict_heeded`` and its context, in the reading the log format supports.

    v1 (``log_version < 2``): the coarse form — heeded iff a verdict was surfaced
    (any non-error check call) and the trial did NOT ship a gate-caught violation.
    Returns ONLY ``{"verdict_heeded": …}`` so a v1 row is byte-identical to the
    pre-v2 output.

    v2: "surfaced" sharpens to "a ``fitness`` line surfaced ≥1 VIOLATION"
    (``violated`` non-empty). With the gate's BLOCKed rule kinds available
    (``shipped_rules``), heeded is the per-rule form — NOT heeded iff a surfaced
    identity's rule kind is among the shipped kinds — and ``verdict_heeded_overlap``
    names those surfaced identities. Without named kinds (the grade's coarse
    fallback path), it degrades to "violation surfaced and did not ship".
    """
    if log_version < _LOG_V2:
        if not surfaced or shipped_violation is None:
            return {"verdict_heeded": None}
        return {"verdict_heeded": not shipped_violation}

    surfaced_ids = _surfaced_identities(calls)
    violations_surfaced = bool(surfaced_ids)
    if not violations_surfaced or shipped_violation is None:
        # Nothing to heed: the tool surfaced no violation (even if the gate blocked
        # something the tool never showed), or the ship outcome is unknown.
        heeded: Optional[bool] = None
        overlap: Optional[list[str]] = None
    elif shipped_rules:
        # Per-rule: match a surfaced identity's KIND (its first '|'-field) against the
        # gate's BLOCKed kinds. from/to is not on the grade side, so kind is the join.
        blocked = set(shipped_rules)
        overlap = sorted(i for i in surfaced_ids if i.split("|", 1)[0] in blocked)
        heeded = len(overlap) == 0
    else:
        # v2 log, but the grade exposed no named BLOCKed kinds (binary_score fallback,
        # or a clean gate): degrade to the coarse "surfaced-and-did-not-ship". overlap
        # stays None — un-nameable, not "no overlap".
        heeded = not shipped_violation
        overlap = None
    return {
        "verdict_heeded": heeded,
        "log_version": log_version,
        "verdict_violations_surfaced": violations_surfaced,
        "verdict_heeded_overlap": overlap,
    }


# --------------------------------------------------------------------------- #
# IO: a single trial artifacts dir
# --------------------------------------------------------------------------- #
def read_trial_artifacts(artifacts_dir) -> tuple[Optional[str], Optional[dict]]:
    """Read ``(mcp_text, trajectory_dict)`` from a trial's artifacts dir.

    Either is ``None`` when its artifact is absent — honest absence, the input the
    funnel core reads as *not applicable*. A present-but-corrupt trajectory is a
    hard error (a lie beats a silent zero): it raises rather than degrading to
    ``None``, so a broken capture is loud, not a fake control-arm null.
    """
    artifacts_dir = Path(artifacts_dir)
    mcp_path = artifacts_dir / MCP_LOG_FILENAME
    traj_path = artifacts_dir / TRAJECTORY_FILENAME
    mcp_text = mcp_path.read_text(encoding="utf-8") if mcp_path.is_file() else None
    trajectory: Optional[dict] = None
    if traj_path.is_file():
        trajectory = json.loads(traj_path.read_text(encoding="utf-8"))
        if not isinstance(trajectory, dict):
            raise ValueError(
                f"{traj_path} is not a JSON object (a corrupt trajectory is refused, "
                "never read as an absent one)"
            )
    return mcp_text, trajectory


# --------------------------------------------------------------------------- #
# IO: an experiment dir (iterate trials from the ledger)
# --------------------------------------------------------------------------- #
def _iter_ledger(ledger_path: Path):
    for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _shipped_violation_from_grade(grade: Optional[dict]) -> Optional[bool]:
    """Did the trial ship a violation the merge-time gate BLOCKed?

    Prefer the groundwork plugin's top-line verdict assertion (``groundwork:verdict``
    == ``failed`` is a BLOCK). Absent it, fall back to the composite command
    holdout's binary score — for this corpus a *functionally correct* exemplar
    fails the holdout ONLY on the gate, so ``binary_score == False`` is a shipped
    violation. ``None`` when the trial is ungraded (ship outcome unknown).
    """
    if grade is None:
        return None
    for a in grade.get("assertions", []):
        if a.get("source") == _GW_PLUGIN_SOURCE and a.get("id") == _GW_VERDICT_ID:
            return a.get("result") == "failed"
    bs = grade.get("binary_score")
    return (not bs) if isinstance(bs, bool) else None


def _shipped_rules_from_grade(grade: Optional[dict]) -> Optional[list[str]]:
    """The rule KINDS the merge-time gate BLOCKed, for the v2 per-rule
    ``verdict_heeded`` — the ids of the groundwork plugin's per-rule assertions that
    ``failed`` (``harness/grade/plugins/groundwork.py`` maps each ``new_violations``
    finding to an assertion whose id IS the rule kind, result ``failed``). The
    top-line ``groundwork:verdict`` assertion is excluded (it is the whole-review
    verdict, not a rule).

    Returns ``None`` — meaning "per-rule kinds unavailable, fall back to coarse" —
    when the grade is absent OR the groundwork plugin contributed no assertions (the
    ``binary_score`` fallback path, which carries no rule breakdown). Returns a
    (possibly empty) sorted list when the plugin DID run: empty ⇒ no rule was
    blocked, which the caller also treats as the coarse case. Matching is on kind
    only — the assertion id has no from/to edge — so edge-level matching is
    best-effort and not attempted here.
    """
    if grade is None:
        return None
    gw = [a for a in grade.get("assertions", []) if a.get("source") == _GW_PLUGIN_SOURCE]
    if not gw:
        return None  # groundwork plugin didn't run (binary_score fallback) ⇒ coarse
    return sorted(
        str(a.get("id"))
        for a in gw
        if a.get("result") == "failed" and a.get("id") != _GW_VERDICT_ID
    )


def iter_experiment_trials(exp_dir) -> list[dict]:
    """Per-trial rows for an experiment dir, in deterministic (sorted) order.

    Reads ``ledger.ndjson`` for the trial records (id / arm / task / artifacts
    path) and the LATEST grade per trial (the ship outcome), then computes the
    funnel from each trial's on-disk artifacts. Sorted by ``(task_id, arm,
    trial_id)`` so the output is byte-stable regardless of ledger interleave.
    """
    exp_dir = Path(exp_dir)
    ledger_path = exp_dir / "ledger.ndjson"
    trials: dict[str, dict] = {}
    grades: dict[str, dict] = {}
    for ev in _iter_ledger(ledger_path):
        kind = ev.get("event")
        if kind == "trial":
            rec = ev.get("trial_record") or {}
            tid = rec.get("trial_id")
            if tid:
                trials[tid] = rec
        elif kind == "grade":
            tid = ev.get("trial_id")
            if tid:
                grades[tid] = ev  # last grade wins

    rows: list[dict] = []
    for tid, rec in trials.items():
        artifacts_path = rec.get("artifacts_path")
        mcp_text, trajectory = (None, None)
        if artifacts_path:
            mcp_text, trajectory = read_trial_artifacts(artifacts_path)
        shipped = _shipped_violation_from_grade(grades.get(tid))
        shipped_rules = _shipped_rules_from_grade(grades.get(tid))
        metrics = compute_trial_metrics(mcp_text, trajectory, shipped, shipped_rules)
        rows.append(
            {
                "trial_id": tid,
                "arm": rec.get("arm"),
                "task_id": rec.get("task_id"),
                "shipped_violation": shipped,
                **metrics,
            }
        )
    rows.sort(key=lambda r: (str(r["task_id"]), str(r["arm"]), str(r["trial_id"])))
    return rows


# --------------------------------------------------------------------------- #
# aggregate + rendering
# --------------------------------------------------------------------------- #
def aggregate(rows: list[dict]) -> dict:
    """Fixed-shape aggregate: per metric, the true/false/null counts and the rate
    over the applicable (non-null) trials (``None`` when none apply)."""
    agg: dict = {"n_trials": len(rows), "n_with_mcp_log": sum(1 for r in rows if r.get("has_mcp_log"))}
    for m in METRIC_IDS:
        vals = [r.get(m) for r in rows]
        n_true = sum(1 for v in vals if v is True)
        n_false = sum(1 for v in vals if v is False)
        n_null = sum(1 for v in vals if v is None)
        denom = n_true + n_false
        agg[m] = {
            "true": n_true,
            "false": n_false,
            "null": n_null,
            "rate": (round(n_true / denom, 4) if denom else None),
        }
    return agg


_CSV_COLUMNS = (
    "trial_id", "arm", "task_id",
    "grounded_before_edit", "checked_after_last_edit", "verdict_heeded",
    "has_mcp_log", "verdict_surfaced", "shipped_violation",
    "n_mcp_calls", "n_file_edits",
)


def _csv_cell(v) -> str:
    """Explicit ``null`` for a not-applicable value (never blank == false)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def rows_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_CSV_COLUMNS)
    for r in rows:
        w.writerow([_csv_cell(r.get(c)) for c in _CSV_COLUMNS])
    return buf.getvalue()


def render_json(rows: list[dict]) -> str:
    """Canonical JSON: key-sorted, so the artifact is byte-deterministic."""
    return json.dumps({"trials": rows, "aggregate": aggregate(rows)}, sort_keys=True, indent=2) + "\n"


# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--experiment", type=Path, help="an experiment dir (iterate its ledgered trials)")
    src.add_argument("--trial", type=Path, help="a single trial artifacts dir")
    ap.add_argument("--shipped-violation", choices=("true", "false", "unknown"), default="unknown",
                    help="for --trial: the merge-time gate outcome (default: unknown → verdict_heeded null)")
    ap.add_argument("--json", type=Path, help="write the per-trial + aggregate JSON here")
    ap.add_argument("--csv", type=Path, help="write the per-trial CSV here")
    args = ap.parse_args(argv)

    if args.experiment is not None:
        rows = iter_experiment_trials(args.experiment)
    else:
        shipped = {"true": True, "false": False, "unknown": None}[args.shipped_violation]
        mcp_text, trajectory = read_trial_artifacts(args.trial)
        rows = [{"trial_id": args.trial.name, "arm": None, "task_id": None,
                 "shipped_violation": shipped,
                 **compute_trial_metrics(mcp_text, trajectory, shipped)}]

    if args.json:
        args.json.write_text(render_json(rows), encoding="utf-8")
    if args.csv:
        args.csv.write_text(rows_to_csv(rows), encoding="utf-8")

    # stdout: the aggregate line (always) + the CSV table when no file sink chosen.
    agg = aggregate(rows)
    print("funnel aggregate: " + json.dumps(agg, sort_keys=True))
    if not args.json and not args.csv:
        sys.stdout.write(rows_to_csv(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
