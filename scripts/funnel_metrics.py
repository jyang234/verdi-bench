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
edit. ``verdict_heeded`` uses the ONE response fact the log does carry —
``isError`` (a non-error ``fitness`` / ``ground`` call means a verdict/card was
produced FOR the agent) — joined with the grade-time gate outcome (whether the
trial *shipped* a violation, from the ledger). It can NOT match "the same rule"
the plan sketches, because the real log carries no rule ids (it logs the call,
not the response) — so "surfaced" means "a verdict was produced", and "shipped"
comes from the merge-time gate. An **absent** MCP log ⇒ every metric is ``null``
(*not applicable* — a control/bare arm never had the surface), NEVER ``false``.
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

# The grade assertion that carries the merge-time gate's top-line verdict
# (harness/grade/plugins/groundwork.py): source ``plugin:groundwork``, id
# ``groundwork:verdict``, result ``failed`` == BLOCK (a shipped violation).
_GW_PLUGIN_SOURCE = "plugin:groundwork"
_GW_VERDICT_ID = "groundwork:verdict"


class MCPCall:
    """One logged ``tools/call`` line from the real ``--log`` transcript."""

    __slots__ = ("name", "arguments", "session", "service", "is_error")

    def __init__(self, name: str, arguments, session, service, is_error: bool) -> None:
        self.name = name
        self.arguments = arguments
        self.session = session
        self.service = service
        self.is_error = is_error


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
        calls.append(
            MCPCall(
                name=name,
                arguments=call.get("arguments"),
                session=obj.get("session"),
                service=obj.get("service"),
                is_error=bool(obj.get("isError", False)),
            )
        )
    return calls


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
) -> dict:
    """The pure funnel core: the three metrics + their context for one trial.

    ``mcp_text`` is the raw ``groundwork-mcp.jsonl`` bytes (``None`` == the log is
    ABSENT — a control/bare arm, not applicable). ``trajectory`` is the parsed v3
    trajectory dict (``None`` == absent). ``shipped_violation`` is the merge-time
    gate outcome (``True`` == the trial shipped a violation the gate BLOCKed;
    ``False`` == clean; ``None`` == unknown, e.g. an ungraded trial). Every metric
    is ``True`` / ``False`` / ``None``; ``None`` is explicit *not-applicable*,
    never laundered into ``False`` (telemetry-null discipline).
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
    # tool produced a verdict/card the agent saw). The real log has no response, so
    # this isError-plus-tool signal is the honest "a verdict was surfaced" fact.
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

    # verdict_heeded: only meaningful once a verdict was surfaced AND the ship
    # outcome is known. No verdict surfaced ⇒ nothing to heed (null). Otherwise it
    # is heeded iff the trial did NOT ship a violation the gate caught.
    if not surfaced or shipped_violation is None:
        verdict_heeded = None
    else:
        verdict_heeded = not shipped_violation

    return {
        "grounded_before_edit": grounded_before_edit,
        "checked_after_last_edit": checked_after_last_edit,
        "verdict_heeded": verdict_heeded,
        "has_mcp_log": True,
        "n_mcp_calls": len(calls),
        "n_file_edits": (len(edits) if edits is not None else None),
        "verdict_surfaced": surfaced,
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
        metrics = compute_trial_metrics(mcp_text, trajectory, shipped)
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
