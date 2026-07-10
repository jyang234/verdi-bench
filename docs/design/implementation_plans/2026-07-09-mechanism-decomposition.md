# Mechanism-Decomposition Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attribute the mechanism of the consistency program's r5 rescue (design of record: `docs/design/mechanism-decomposition-program.md`) — a free decomposed regrade, a placebo-gate arm, a policy-pointer arm, a de-baited r5 variant, plus a prereg-hash lock rider.

**Architecture:** Two new payload-gated treatments in the `claude-code-groundwork` trial image (a static-reason Stop hook; a prompt-only pointer token), registered in the flagship authoring kit; a standalone stdlib analysis script that re-executes the fused holdout's two halves separately in the pinned grader image; one additive optional field on the lock event; one new checked-in corpus task. Experiments are authored/run with the existing five-command lifecycle.

**Tech Stack:** Python 3.11+ (3.12-compatible), `uv`, `pytest`, docker CLI, the pinned `claude` CLI trial image, `go` + pinned `flowmap`/`groundwork` binaries (corpus rebuild only).

## Global Constraints

- `make verify` (all tests + import contracts) after every task, before its commit. Not skippable.
- TDD reproduce-first: each task's failing test is written and seen failing before the implementation.
- Typed Python, `from __future__ import annotations`, module docstrings citing the design doc.
- Treatment texts and hook scripts are **byte-stable pre-registered treatment definitions**: the exact strings in this plan are the treatment; no rewording during implementation.
- Ledger changes are **additive omit-if-None only**; no event schema/serialization/hash-chain change; historical chains must still `verify-chain` OK.
- The deterministic grading path imports no LLM client (existing import contract stays green).
- Fail loudly: unknown payload shapes/workflows/extras raise naming the offender; no silent control fallback beyond the existing documented fail-closed rule.
- No wall-clock/randomness in any emitted artifact.
- Operational (spend/keys/docker) steps are marked **OPERATOR**; they need `.env` keys, a docker daemon, and respect per-experiment cost ceilings. Total new spend ceiling for the program: $15.
- Model for all new experiments: `anthropic/claude-haiku-4-5-20251001`.
- Test changes to existing tests are pre-approved by the design doc and listed per task under **Test change register** — restate them in the commit message.

---

### Task 1: Prereg-hash lock rider

Hash `PRE-REGISTRATION.md` (when present beside the spec) into the lock event, mirroring `rubric_sha256` exactly. Additive, omit-if-None.

**Files:**
- Modify: `harness/plan/lock.py` (add `commit_prereg`, call it in `lock_experiment`)
- Modify: `harness/ledger/events.py:368-405` (`record_experiment_locked` gains `prereg_sha256`)
- Test: `tests/test_eval3_lock.py`

**Interfaces:**
- Consumes: existing `lock_experiment(spec_path, ledger_path, *, ctx, ...)`, `record_experiment_locked(..., rubric_sha256=None)`.
- Produces: `commit_prereg(spec_path) -> Optional[str]`; lock events carry optional top-level `prereg_sha256`; `PREREG_FILENAME = "PRE-REGISTRATION.md"` constant in `harness/plan/lock.py`. Tasks 6 and 8 rely on `bench plan` picking the file up automatically.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eval3_lock.py` (uses that file's existing `write_experiment_yaml`/`ctx_for`/`FAST` fixtures):

```python
def test_lock_commits_prereg_sha_when_present(tmp_path):
    """Design doc `mechanism-decomposition-program.md` rider: PRE-REGISTRATION.md
    beside the spec is hashed into the lock event (bytes, like spec_sha256)."""
    import hashlib

    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    prereg = tmp_path / "PRE-REGISTRATION.md"
    prereg.write_text("# H1: placebo <= 2/12 -> findings content is the mechanism\n",
                      encoding="utf-8")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=ctx_for(tmp_path), **FAST)
    ev = find_events(ledger, events.EXPERIMENT_LOCKED)[0]
    assert ev["prereg_sha256"] == hashlib.sha256(prereg.read_bytes()).hexdigest()


def test_lock_omits_prereg_sha_when_absent(tmp_path):
    """No prereg file -> the field is absent (omit-if-None), so historical
    ledgers and prereg-less experiments render identically to today."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=ctx_for(tmp_path), **FAST)
    ev = find_events(ledger, events.EXPERIMENT_LOCKED)[0]
    assert "prereg_sha256" not in ev
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval3_lock.py -k prereg -q`
Expected: 2 FAIL (`KeyError: 'prereg_sha256'` on the first; the second may pass already — if it passes, keep it: it pins the compatibility contract).

- [ ] **Step 3: Implement**

In `harness/ledger/events.py`, extend `record_experiment_locked` (mirror `rubric_sha256` byte-for-byte — same default, same pass-through position):

```python
    rubric_sha256: Optional[str] = None,
    prereg_sha256: Optional[str] = None,
) -> dict:
```
…and in the `build_event(...)` call add `prereg_sha256=prereg_sha256,` after `rubric_sha256=rubric_sha256,`. Extend the docstring's additive omit-if-None list: `` `prereg_sha256` (mechanism-decomposition rider) commits the prose PRE-REGISTRATION.md bytes so post-lock prereg edits are detectable; absent = no prereg file existed at lock``.

Note: `build_event` drops None-valued kwargs (that is how `task_commitment`/`rubric_sha256` omit today). If any event-shape model elsewhere enumerates lock-event fields, extend it identically — check with `grep -rn "rubric_sha256" harness/` and mirror every hit.

In `harness/plan/lock.py`, after `commit_rubric` (module-level constant near the top):

```python
# The prose pre-registration companion file, hashed into the lock when present
# [design: docs/design/mechanism-decomposition-program.md, rider]. Prose preregs
# were previously unhashed (gitignored, mtimes forgeable — independent review
# §3.7); committing the bytes makes post-lock edits detectable. Optional:
# absence is honest (not every experiment carries prose) and never refuses.
PREREG_FILENAME = "PRE-REGISTRATION.md"


def commit_prereg(spec_path) -> Optional[str]:
    """Preflight: commit the prose pre-registration's content hash, if present.

    Bytes-hash (like ``spec_sha256``), not normalized text: the file is
    instrument-side prose, never checked out cross-platform by the grader.
    ``None`` when no ``PRE-REGISTRATION.md`` sits beside the spec — an honestly
    absent prereg, omitted from the event rather than recorded as a sentinel.
    """
    prereg_path = Path(spec_path).parent / PREREG_FILENAME
    if not prereg_path.is_file():
        return None
    return _sha256_bytes(prereg_path.read_bytes())
```

In `lock_experiment`, after step 6 (`commit_rubric`):

```python
    rubric_sha256 = commit_rubric(spec_path, spec)    # 6. rubric commitment [D-P7-6]
    prereg_sha256 = commit_prereg(spec_path)          # 6b. prereg commitment [rider]
```
…and pass `prereg_sha256=prereg_sha256,` to `events.record_experiment_locked(...)` after `rubric_sha256=rubric_sha256,`.

- [ ] **Step 4: Run tests, then historical-chain compatibility check**

Run: `uv run pytest tests/test_eval3_lock.py -q`
Expected: all PASS.
Run: `uv run bench verify-chain runs/consistency/reach-confirm/ledger.ndjson`
Expected: chain OK (proves old ledgers unaffected).

- [ ] **Step 5: `make verify`, commit**

```bash
make verify
git add harness/plan/lock.py harness/ledger/events.py tests/test_eval3_lock.py
git commit -m "feat(plan): hash PRE-REGISTRATION.md into the lock event (additive rider)"
```

---

### Task 2: `decompose_scores.py` — retrospective score decomposition

Standalone analysis script re-executing the fused holdout's two halves separately per graded trial, inside the pinned grader image. No ledger mutation.

**Files:**
- Create: `scripts/flagship/decompose_scores.py`
- Test: `tests/test_flagship_decompose_scores.py`

**Interfaces:**
- Consumes: ledger `trial` events (`ev["event"] == "trial"`, `ev["trial_record"]` with keys `trial_id`, `task_id`, `arm`, `artifacts_path`; workspace = `Path(artifacts_path).parent`) and `grade` events (`ev["trial_id"]`, `ev["binary_score"]`, `ev["assertions"]` — advisory gate assertion has `id == "groundwork:verdict"`); the per-experiment `holdouts/<task>/holdout.json` command shape emitted by `corpora/groundwork-v0/build_tasks.py::holdout_argv` (`["sh","-c","set -e; H=...; cp ...; go test ./...; verdi-groundwork-check <id>"]`); grader image from `$VERDI_GRADER_IMAGE`.
- Produces: `split_holdout_argv(argv) -> tuple[list[str], list[str]]`, `load_graded_trials(ledger_path) -> list[dict]` (pure, unit-tested); CLI writing `runs/consistency/DECOMPOSITION.json` + `runs/consistency/DECOMPOSITION.md`. Tasks 6/8/9 re-run it over new experiments.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_flagship_decompose_scores.py`:

```python
"""Retrospective score decomposition — pure-core tests (no docker, no ledgers).

Pins the two pure seams of scripts/flagship/decompose_scores.py: the fused
holdout-script split (functional half / gate half) against the REAL corpus
builder's argv shape, and the ledger walk that joins trial and grade events.
The docker re-execution itself is validated operationally: the script recomputes
fused = functional AND gate and refuses (nonzero exit) on any mismatch with the
recorded binary_score.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts" / "flagship"))
sys.path.insert(0, str(_REPO / "corpora" / "groundwork-v0"))

import build_tasks  # noqa: E402  (stdlib-only; import needs no binaries)
import decompose_scores as ds  # noqa: E402


def test_split_round_trips_the_real_builder_argv():
    argv = build_tasks.holdout_argv(
        "gw-r5", [("internal/wire/feature_test.go", "package wire\n")]
    )
    functional, gate = ds.split_holdout_argv(argv)
    assert functional[:2] == ["sh", "-c"] and gate[:2] == ["sh", "-c"]
    assert functional[2].endswith("go test ./...")
    assert "verdi-groundwork-check gw-r5" in gate[2]
    assert "verdi-groundwork-check" not in functional[2]
    assert "go test" not in gate[2]
    # the functional half keeps the holdouts-root binding + the cp injection
    assert 'H="${VERDI_HOLDOUTS_DIR:-/holdouts}"' in functional[2]
    assert "cp " in functional[2]
    # the gate half stays fail-fast
    assert gate[2].startswith("set -e; ")


def test_split_refuses_unexpected_shapes():
    with pytest.raises(ValueError, match="argv shape"):
        ds.split_holdout_argv(["bash", "-c", "true"])
    with pytest.raises(ValueError, match="holdout script"):
        ds.split_holdout_argv(["sh", "-c", "echo hi; true"])


def test_load_graded_trials_joins_trial_and_grade(tmp_path):
    ledger = tmp_path / "ledger.ndjson"
    rows = [
        {"event": "experiment_locked", "spec_sha256": "x"},
        {"event": "trial", "trial_record": {
            "trial_id": "trial-a", "task_id": "gw-r5", "arm": "haiku-bare",
            "artifacts_path": "runs/x/workspaces/trial-a/artifacts"}},
        {"event": "trial", "trial_record": {
            "trial_id": "trial-ungraded", "task_id": "gw-r5", "arm": "haiku-bare",
            "artifacts_path": "runs/x/workspaces/trial-ungraded/artifacts"}},
        {"event": "grade", "trial_id": "trial-a", "binary_score": False,
         "assertions": [
             {"id": "gw-r5-functional-groundwork", "source": "holdout_test",
              "result": "fail", "detail": "verdi-groundwork-check: BLOCK"},
             {"id": "groundwork:verdict", "source": "plugin:groundwork",
              "result": "fail", "detail": "groundwork review verdict: BLOCK"},
         ]},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    trials = ds.load_graded_trials(ledger)
    assert len(trials) == 1  # ungraded trials are excluded, not guessed at
    t = trials[0]
    assert t["trial_id"] == "trial-a"
    assert t["task_id"] == "gw-r5"
    assert t["arm"] == "haiku-bare"
    assert t["workspace"] == "runs/x/workspaces/trial-a"
    assert t["binary_score"] is False
    assert t["advisory_verdict"] == "groundwork review verdict: BLOCK"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_flagship_decompose_scores.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'decompose_scores'`.

- [ ] **Step 3: Write the script**

Create `scripts/flagship/decompose_scores.py`:

```python
#!/usr/bin/env python3
"""Decompose the fused groundwork holdout score, retrospectively and offline.

[design: docs/design/mechanism-decomposition-program.md, piece 0]

Every groundwork-v0 holdout is ONE fused command — feature tests AND the
structural gate (`set -e; …; go test ./...; verdi-groundwork-check <task>`) —
so `binary_score` conflates two channels (independent review §3.1). This script
re-executes the two halves SEPARATELY against each graded trial's preserved
workspace, inside the pinned grader image, and emits a decomposed table:
functional-pass / gate-pass / fused per task x arm x experiment.

Ground rules:
  * NO ledger mutation — the chains are immutable; output is an analysis
    artifact (DECOMPOSITION.json + DECOMPOSITION.md beside the experiments).
  * Self-validating: recomputed fused (functional AND gate) must equal the
    recorded `binary_score` for every trial; any mismatch is listed and the
    exit code is nonzero. A silent divergence would be a wrong instrument.
  * Workspaces are graded on a throwaway copy (mirroring DockerGradeRunner's
    fail-safe posture), network-less, holdouts mounted read-only.

Usage:
    VERDI_GRADER_IMAGE=<digest> uv run python scripts/flagship/decompose_scores.py \
        runs/consistency/recon2 runs/consistency/instructed ... \
        [--out runs/consistency]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GRADER_IMAGE_ENV = "VERDI_GRADER_IMAGE"
DOCKER_TIMEOUT_S = 600


def split_holdout_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split the fused corpus holdout into (functional, gate) argvs.

    The builder's shape (build_tasks.holdout_argv) is
    ``["sh","-c","set -e; H=…; <cps>; go test ./...; <wrapper> <task>"]``:
    the gate call is the final ``"; "``-separated segment and the functional
    half is everything before it. Any other shape is refused loudly — a
    guessed split would silently mis-score."""
    if len(argv) != 3 or argv[:2] != ["sh", "-c"]:
        raise ValueError(f"unexpected holdout argv shape: {argv!r}")
    script = argv[2]
    head, sep, gate = script.rpartition("; ")
    if not sep or not head.endswith("go test ./..."):
        raise ValueError(f"unexpected fused holdout script: {script!r}")
    return ["sh", "-c", head], ["sh", "-c", "set -e; " + gate]


def load_graded_trials(ledger_path) -> list[dict]:
    """Join trial and grade events: one row per GRADED trial.

    Ungraded trials are excluded (never guessed at). The advisory gate verdict
    (``id == "groundwork:verdict"``, source ``plugin:groundwork``) rides along
    for the cross-check column; absent stays ``None``."""
    trials: dict[str, dict] = {}
    grades: dict[str, dict] = {}
    for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("event") == "trial":
            tr = ev["trial_record"]
            trials[tr["trial_id"]] = {
                "trial_id": tr["trial_id"],
                "task_id": tr["task_id"],
                "arm": tr["arm"],
                "workspace": str(Path(tr["artifacts_path"]).parent),
            }
        elif ev.get("event") == "grade":
            grades[ev["trial_id"]] = ev
    rows: list[dict] = []
    for tid, t in sorted(trials.items()):
        g = grades.get(tid)
        if g is None:
            continue
        advisory = next(
            (a for a in g.get("assertions", []) if a.get("id") == "groundwork:verdict"),
            None,
        )
        rows.append({
            **t,
            "binary_score": g["binary_score"],
            "advisory_verdict": advisory.get("detail") if advisory else None,
        })
    return rows


def run_half_in_grader(image: str, workspace: str, holdouts_dir: Path,
                       argv: list[str]) -> tuple[bool, str]:
    """Run one holdout half in the grader image against a THROWAWAY workspace
    copy (never the ledgered original), network-less, holdouts read-only."""
    with tempfile.TemporaryDirectory() as td:
        ws_copy = Path(td) / "workspace"
        shutil.copytree(workspace, ws_copy, symlinks=True)
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network=none",
             "-v", f"{ws_copy}:/workspace",
             "-v", f"{holdouts_dir.resolve()}:/holdouts:ro",
             "-w", "/workspace", image, *argv],
            capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S,
        )
    return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()[-400:]


def decompose_experiment(exp_dir: Path, image: str) -> list[dict]:
    ledger = exp_dir / "ledger.ndjson"
    rows = []
    for t in load_graded_trials(ledger):
        holdouts_dir = exp_dir / "holdouts" / t["task_id"]
        declared = json.loads((holdouts_dir / "holdout.json").read_text(encoding="utf-8"))
        functional_argv, gate_argv = split_holdout_argv(declared["argv"])
        f_ok, f_detail = run_half_in_grader(image, t["workspace"], holdouts_dir,
                                            functional_argv)
        g_ok, g_detail = run_half_in_grader(image, t["workspace"], holdouts_dir,
                                            gate_argv)
        rows.append({
            **t,
            "experiment": exp_dir.name,
            "functional_pass": f_ok,
            "gate_pass": g_ok,
            "fused_recomputed": f_ok and g_ok,
            "fused_matches_recorded": (f_ok and g_ok) == t["binary_score"],
            "functional_detail": None if f_ok else f_detail,
            "gate_detail": None if g_ok else g_detail,
        })
        print(f"  {exp_dir.name} {t['trial_id']} {t['task_id']} {t['arm']}: "
              f"functional={'PASS' if f_ok else 'fail'} gate={'PASS' if g_ok else 'fail'}"
              + ("" if rows[-1]["fused_matches_recorded"] else "  ** MISMATCH **"))
    return rows


def render_markdown(rows: list[dict]) -> str:
    """Per experiment x task x arm: functional / gate / fused pass counts."""
    cells: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        key = (r["experiment"], r["task_id"], r["arm"])
        c = cells.setdefault(key, {"n": 0, "functional": 0, "gate": 0, "fused": 0})
        c["n"] += 1
        c["functional"] += r["functional_pass"]
        c["gate"] += r["gate_pass"]
        c["fused"] += r["fused_recomputed"]
    lines = [
        "# Decomposed scores (functional vs gate) — retrospective regrade",
        "",
        "> Generated by `scripts/flagship/decompose_scores.py`; no ledger was",
        "> mutated. `fused = functional AND gate` reproduces the recorded",
        "> `binary_score` on every row unless a MISMATCH is flagged below.",
        "",
        "| experiment | task | arm | n | functional | gate | fused |",
        "|---|---|---|--:|--:|--:|--:|",
    ]
    for (exp, task, arm), c in sorted(cells.items()):
        lines.append(f"| {exp} | {task} | {arm} | {c['n']} | "
                     f"{c['functional']}/{c['n']} | {c['gate']}/{c['n']} | "
                     f"{c['fused']}/{c['n']} |")
    mismatches = [r for r in rows if not r["fused_matches_recorded"]]
    lines += ["", f"Trials recomputed: {len(rows)}; "
              f"fused-vs-recorded mismatches: {len(mismatches)}"]
    for r in mismatches:
        lines.append(f"- MISMATCH {r['experiment']}/{r['trial_id']} "
                     f"({r['task_id']}, {r['arm']}): recomputed "
                     f"{r['fused_recomputed']} vs recorded {r['binary_score']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("experiments", nargs="+", type=Path,
                    help="experiment dirs (each with ledger.ndjson + holdouts/)")
    ap.add_argument("--out", type=Path, default=Path("runs/consistency"),
                    help="directory for DECOMPOSITION.json / DECOMPOSITION.md")
    args = ap.parse_args()
    image = os.environ.get(GRADER_IMAGE_ENV)
    if not image:
        print(f"REFUSED: set {GRADER_IMAGE_ENV} to the pinned grader image "
              "(the digest the program graded with)", file=sys.stderr)
        return 2
    rows: list[dict] = []
    for exp in args.experiments:
        rows.extend(decompose_experiment(exp, image))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "DECOMPOSITION.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / "DECOMPOSITION.md").write_text(render_markdown(rows), encoding="utf-8")
    mismatches = sum(not r["fused_matches_recorded"] for r in rows)
    print(f"\nwrote {args.out}/DECOMPOSITION.md ({len(rows)} trials, "
          f"{mismatches} mismatches)")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_flagship_decompose_scores.py -q`
Expected: 3 PASS.

- [ ] **Step 5: `make verify`, commit**

```bash
make verify
git add scripts/flagship/decompose_scores.py tests/test_flagship_decompose_scores.py
git commit -m "feat(flagship): retrospective decomposed regrade of the fused holdout score"
```

- [ ] **Step 6 (OPERATOR): run the retrospective decomposition**

Grader image digest: the program's pinned grader (`sha256:5da3a95221d2…` — full digest in `docs/WALKTHROUGH.md` Appendix B pins; also recorded per-task in each ledger's `task_shas`).

```bash
VERDI_GRADER_IMAGE=<pinned grader digest> uv run python scripts/flagship/decompose_scores.py \
  runs/consistency/recon runs/consistency/recon2 runs/consistency/instructed \
  runs/consistency/smoke-enforced runs/consistency/reach-enforced \
  runs/consistency/reach-confirm runs/consistency/reach-sonnet
```
Expected: exit 0, 0 mismatches, `runs/consistency/DECOMPOSITION.md` reproducing the independent review's GATEBLOCK table (§2.2): r2/r5 haiku bare failures 100% gate-only with functional passing. Investigate any mismatch before proceeding — a mismatch means either non-reproducible grading or a script bug; do not hand-wave it.

---

### Task 3: `placebo_gate` treatment in the trial image

The mechanism-decomposition control: rung-3's exact hook machinery, but the hook runs no gate and blocks with one static, content-free reason.

**Files:**
- Modify: `images/reference/claude-code-groundwork/agent.py` (new `PLACEBO_WORKFLOW` + `PLACEBO_HOOK_PY` constants; `WORKFLOW_PROMPT_KEY` entry; `plan_groundwork` branch at the current `if workflow == ENFORCED_WORKFLOW:` block, `agent.py:474-493`)
- Test: `tests/test_image_claude_code_groundwork.py`

**Interfaces:**
- Consumes: existing `ENFORCED_WORKFLOW`, `render_settings`, `GroundworkPlan`, `WORKFLOW_PROMPT_KEY` (`agent.py:148-151`), the enforced-arm plan branch.
- Produces: `agent.PLACEBO_WORKFLOW == "placebo_gate"`, `agent.PLACEBO_HOOK_PY` (byte-stable treatment constant). Payload `{"tools": ["groundwork"], "workflow": "placebo_gate"}` arms: rung-2 argv verbatim + Stop hook = `PLACEBO_HOOK_PY`, **no** base-graph/policy `file_copies`. Task 5's kit entry emits exactly this payload.

- [ ] **Step 1: Write the failing tests**

In `tests/test_image_claude_code_groundwork.py`, beside the existing `_ENFORCED` constant add:

```python
_PLACEBO = {"tools": ["groundwork"], "workflow": "placebo_gate"}
```

Then append (near `test_enforced_plan_arms_stop_hook_under_home_and_argv_equals_rung2`):

```python
# --- placebo_gate: the mechanism-decomposition control ----------------------
# [design: docs/design/mechanism-decomposition-program.md, piece 1] The placebo
# arm is byte-identical to rung 3 EXCEPT the hook: same tools, same rung-2
# prompt token, same rounds/settings machinery — but the hook runs NO gate and
# blocks with ONE static, content-free reason. Any rescue it produces is
# attributable to forced re-review alone.


def test_placebo_plan_arms_static_hook_without_gate_inputs():
    plan = agent.plan_groundwork(_PLACEBO, home="/h", workspace="/w")
    files = dict(plan.files)
    assert files["/h/verdi-enforce/rounds"] == "0"
    assert files["/h/verdi-enforce/stop_hook.py"] == agent.PLACEBO_HOOK_PY
    assert "/h/.claude/settings.json" in files
    # the placebo reads no gate inputs: no tamper-proof base graph/policy copies
    assert plan.file_copies == ()


def test_placebo_argv_is_byte_identical_to_rung2_and_rung3():
    args = {
        wf: agent.plan_groundwork(p, home="/h", workspace="/w").cli_extra_args
        for wf, p in (("r2", _TREATMENT), ("r3", _ENFORCED), ("placebo", _PLACEBO))
    }
    assert args["placebo"] == args["r2"] == args["r3"]


def test_placebo_hook_is_valid_python_and_runs_no_gate():
    compile(agent.PLACEBO_HOOK_PY, "stop_hook.py", "exec")
    # structurally incapable of consulting the gate or the map
    assert "subprocess" not in agent.PLACEBO_HOOK_PY
    assert "/opt/groundwork/bin" not in agent.PLACEBO_HOOK_PY
    assert "flowmap" not in agent.PLACEBO_HOOK_PY
    assert "policy.json" not in agent.PLACEBO_HOOK_PY
    assert "graph" not in agent.PLACEBO_HOOK_PY


def test_placebo_block_reason_is_static_and_content_free():
    assert (
        "Review your changes for policy compliance before finishing."
        in agent.PLACEBO_HOOK_PY
    )
    # never leaks rule names, findings vocabulary, or verdict strings
    for token in ("must_not_reach", "no_concurrent_reach", "must_pass_through",
                  "io_budget", "STRUCTURALLY", "new_violations"):
        assert token not in agent.PLACEBO_HOOK_PY


def test_placebo_without_groundwork_tool_raises():
    with pytest.raises(ValueError, match="workflow"):
        agent.plan_groundwork({"workflow": "placebo_gate"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_image_claude_code_groundwork.py -k placebo -q`
Expected: 5 FAIL (first four `AttributeError: … has no attribute 'PLACEBO_HOOK_PY'` / `ValueError: unknown workflow 'placebo_gate'`; the last currently fails because the *unknown-workflow* error fires instead of the tool-less one — after implementation the tool-less refusal covers it).

- [ ] **Step 3: Implement in `agent.py`**

After the `ENFORCED_WORKFLOW` constant (`agent.py:142`):

```python
# The mechanism-decomposition PLACEBO workflow [design:
# docs/design/mechanism-decomposition-program.md, piece 1]. Same payload shape,
# same rung-2 prompt text, same Stop-hook machinery as rung 3 — but the hook is
# :data:`PLACEBO_HOOK_PY`: no gate run, no graph/policy inputs, one static
# content-free block reason. The rung3-vs-placebo contrast isolates the gate's
# FINDINGS CONTENT from the forcing function itself.
PLACEBO_WORKFLOW = "placebo_gate"
```

Extend `WORKFLOW_PROMPT_KEY` (the placebo reuses rung 2's text verbatim, exactly as the enforced rung does — same-argv parity is the treatment design):

```python
WORKFLOW_PROMPT_KEY = {
    "ground_verify": "ground_verify",
    ENFORCED_WORKFLOW: "ground_verify",
    PLACEBO_WORKFLOW: "ground_verify",
}
```

After the `ENFORCEMENT_HOOK_PY` constant, add the placebo hook (BYTE-STABLE — this exact text is the pre-registered treatment):

```python
# The placebo Stop-hook script — BYTE-STABLE, PART OF THE PRE-REGISTERED PLACEBO
# TREATMENT DEFINITION: any edit here is a NEW pre-registration. Identical
# machinery to ENFORCEMENT_HOOK_PY (rounds counter, MAX_ROUNDS, enforce log,
# never traps) minus the gate: no subprocess, no graph, no policy — it blocks
# every Stop attempt with ONE static, content-free reason until the budget is
# exhausted, then allows. INVARIANT: the reason must never name a rule, a
# finding, a verdict string, or any task property — content-free is the point.
PLACEBO_HOOK_PY = r'''#!/usr/bin/env python3
"""verdi placebo-gate Stop hook — PART OF THE PRE-REGISTERED PLACEBO TREATMENT
DEFINITION; any edit here is a NEW pre-registration, never a casual change.

The mechanism-decomposition control for rung-3 enforcement: the SAME bounded
Stop-hook machinery (rounds counter, MAX_ROUNDS, enforce log, never traps),
but it consults NO gate and reads NO inputs. It blocks every Stop attempt with
one STATIC, content-free reason until the budget is exhausted, then allows.
Any rescue this treatment produces is attributable to forced re-review alone,
never to gate findings.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUNDS = os.path.join(HERE, "rounds")
ENFORCE_LOG = "/workspace/artifacts/groundwork-enforce.jsonl"
MAX_ROUNDS = 3
REASON = "Review your changes for policy compliance before finishing."


def log(record):
    os.makedirs(os.path.dirname(ENFORCE_LOG), exist_ok=True)
    with open(ENFORCE_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def main():
    current = int(open(ROUNDS, encoding="utf-8").read().strip())
    if current >= MAX_ROUNDS:
        log({"round": current, "decision": "exhausted"})
        sys.exit(0)
    with open(ROUNDS, "w", encoding="utf-8") as fh:
        fh.write(str(current + 1))
    log({"round": current, "decision": "block", "findings": REASON})
    print(json.dumps({"decision": "block", "reason": REASON}))
    sys.exit(0)


try:
    main()
except SystemExit:
    raise
except Exception as exc:  # never crash the session: allow on any unexpected error
    try:
        log({"round": -1, "decision": "hook_error", "detail": repr(exc)[-800:]})
    except Exception:
        pass
    sys.exit(0)
'''
```

Replace the enforced-only branch in `plan_groundwork` (`agent.py:474-493`):

```python
    if workflow in (ENFORCED_WORKFLOW, PLACEBO_WORKFLOW):
        # rung 3 / placebo = rung 2 + a Stop hook, realized PURELY in arm-time
        # filesystem (all under $HOME; argv is byte-identical to rung 2). The
        # ENFORCED hook needs the pristine BASE graph + graded policy preserved
        # tamper-proof beside it; the PLACEBO hook consults nothing, so it gets
        # no copies — an unread input staged anyway would blur the contrast.
        enforce_dir = os.path.join(home, "verdi-enforce")
        hook_script = os.path.join(enforce_dir, "stop_hook.py")
        settings_path = os.path.join(home, ".claude", "settings.json")
        hook_source = (
            ENFORCEMENT_HOOK_PY if workflow == ENFORCED_WORKFLOW else PLACEBO_HOOK_PY
        )
        mkdirs = (*mkdirs, enforce_dir)
        files = (
            *files,
            (os.path.join(enforce_dir, "rounds"), "0"),
            (hook_script, hook_source),
            (settings_path, render_settings(hook_script)),
        )
        if workflow == ENFORCED_WORKFLOW:
            file_copies = (
                (os.path.join(workspace, GRAPH_NAME), os.path.join(enforce_dir, "base.graph.json")),
                (os.path.join(workspace, POLICY_NAME), os.path.join(enforce_dir, "policy.json")),
            )
```

Also update the module docstring's workflow list and the `plan_groundwork` docstring to mention `placebo_gate` (one sentence each, citing the design doc).

- [ ] **Step 4: Run the full image test file**

Run: `uv run pytest tests/test_image_claude_code_groundwork.py -m "not docker" -q`
Expected: all PASS (including the pre-existing enforced/lower-rung tests — `test_lower_rungs_arm_no_stop_hook_or_settings` must still pass untouched).

- [ ] **Step 5: `make verify`, commit**

```bash
make verify
git add images/reference/claude-code-groundwork/agent.py tests/test_image_claude_code_groundwork.py
git commit -m "feat(images): placebo_gate workflow — static-reason Stop hook, no gate inputs"
```

---

### Task 4: `policy_pointer` treatment in the trial image

Prompt-only treatment: one appended system-prompt line pointing at `policy.json`; no tools, no MCP config, no hook. Cannot ride the `workflow` mechanism (that path couples the prompt to groundwork tools and refuses tool-less workflows), so it is a new payload key with its own arming path.

**Files:**
- Modify: `images/reference/claude-code-groundwork/agent.py` (new `SYSTEM_PROMPT_EXTRAS` registry; a `system_prompt_extra` branch in `plan_groundwork` between the workflow validation and the `if not enabled:` return, `agent.py:438`)
- Test: `tests/test_image_claude_code_groundwork.py`

**Interfaces:**
- Consumes: `GroundworkPlan` (disabled plans no-op in `apply_plan`/`cli_env`; `cli_argv` uses `plan.cli_extra_args` unconditionally).
- Produces: `agent.SYSTEM_PROMPT_EXTRAS` dict with key `"policy_pointer"` (byte-stable text). Payload `{"system_prompt_extra": "policy_pointer"}` → disabled plan whose `cli_extra_args` is exactly one `--append-system-prompt=<text>` token. Task 5's kit entry emits this payload.

- [ ] **Step 1: Write the failing tests**

Beside the payload constants add:

```python
_POINTER = {"system_prompt_extra": "policy_pointer"}
```

Append tests:

```python
# --- policy_pointer: the cheapest-possible treatment -------------------------
# [design: docs/design/mechanism-decomposition-program.md, piece 2] Prompt-only:
# one appended system-prompt line pointing at policy.json — no tools, no MCP
# config, no hook, no filesystem writes. A rung below availability on the
# integration ladder ("rung 1.5" if it works, a null that strengthens the
# forcing-function story if it doesn't).


def test_pointer_argv_is_control_plus_exactly_one_prompt_token():
    plan = agent.plan_groundwork(_POINTER, home="/h", workspace="/w")
    control = agent.plan_groundwork({}, home="/h", workspace="/w")
    argv = agent.cli_argv("do the task", plan, "m1")
    control_argv = agent.cli_argv("do the task", control, "m1")
    delta = [t for t in argv if t not in control_argv]
    assert delta == [
        "--append-system-prompt=" + agent.SYSTEM_PROMPT_EXTRAS["policy_pointer"]
    ]
    assert not any(t.startswith("--mcp-config") for t in argv)


def test_pointer_plan_is_otherwise_disabled_and_writes_nothing(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    workspace.mkdir()
    plan = agent.plan_groundwork(_POINTER, home=str(home), workspace=str(workspace))
    assert plan.enabled is False
    assert plan.files == () and plan.copies == () and plan.file_copies == ()
    assert plan.symlinks == () and plan.mkdirs == ()
    agent.apply_plan(plan)  # must be a no-op
    assert not home.exists()
    assert list(workspace.iterdir()) == []
    # env untouched: no bin dir prepended
    assert agent.cli_env({"PATH": "/usr/bin"}, plan) == {"PATH": "/usr/bin"}


def test_pointer_text_is_pinned_and_process_only():
    text = agent.SYSTEM_PROMPT_EXTRAS["policy_pointer"]
    assert text == (
        "This repository declares structural policy in `policy.json`; "
        "your change must honor it."
    )
    # names no tool, no workflow step, no task property
    for token in ("mcp__", "groundwork", "flowmap", "verify"):
        assert token not in text


def test_pointer_combined_with_tools_or_workflow_raises():
    with pytest.raises(ValueError, match="system_prompt_extra"):
        agent.plan_groundwork(
            {"system_prompt_extra": "policy_pointer", "tools": ["groundwork"]}
        )
    with pytest.raises(ValueError):
        agent.plan_groundwork(
            {"system_prompt_extra": "policy_pointer", "workflow": "ground_verify"}
        )


def test_unknown_system_prompt_extra_raises():
    with pytest.raises(ValueError, match="unknown system_prompt_extra"):
        agent.plan_groundwork({"system_prompt_extra": "not-a-registered-extra"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_image_claude_code_groundwork.py -k pointer -q`
Expected: 5 FAIL (`AttributeError: … 'SYSTEM_PROMPT_EXTRAS'`, and the combined/unknown cases currently return a silent disabled plan — the exact fail-closed-but-silent shape this task replaces with loud refusals).

- [ ] **Step 3: Implement in `agent.py`**

After `WORKFLOW_PROMPT_KEY`:

```python
# Prompt-only treatments, keyed by ``payload.system_prompt_extra`` [design:
# docs/design/mechanism-decomposition-program.md, piece 2]. Delivered as one
# ``--append-system-prompt=<text>`` token with NO tools, NO MCP config, NO hook,
# NO filesystem writes — the arm is otherwise byte-for-byte the control. These
# texts are PRE-REGISTERED TREATMENT DEFINITIONS: byte-stable, process-only
# (they may point at an agent-visible file; they never name a tool, a workflow
# step, a trap, or an expected answer). Registry membership is the known set —
# an unknown value is refused loudly, and combining a prompt-only treatment
# with tools/workflow is refused (it would blur which treatment ran).
SYSTEM_PROMPT_EXTRAS = {
    "policy_pointer": (
        "This repository declares structural policy in `policy.json`; "
        "your change must honor it."
    ),
}
```

In `plan_groundwork`, insert between the workflow-validation block and `if not enabled:` (`agent.py:438`):

```python
    extra = payload.get("system_prompt_extra")
    if extra is not None:
        if enabled or has_workflow:
            raise ValueError(
                f"payload declares system_prompt_extra {extra!r} alongside "
                "groundwork tools/workflow — prompt-only treatments are "
                "exclusive by definition; a combined arm would blur which "
                "treatment ran"
            )
        if extra not in SYSTEM_PROMPT_EXTRAS:
            raise ValueError(
                f"unknown system_prompt_extra {extra!r}; known extras: "
                f"{sorted(SYSTEM_PROMPT_EXTRAS)}"
            )
        return GroundworkPlan(
            enabled=False,
            cli_extra_args=(
                f"--append-system-prompt={SYSTEM_PROMPT_EXTRAS[extra]}",
            ),
        )
```

Update the `GroundworkPlan`/`plan_groundwork`/module docstrings: the disabled-plan argv-delta statement gains "…except a registered `system_prompt_extra`, which is a disabled plan carrying exactly one `--append-system-prompt` token" (one sentence, citing the design doc).

- [ ] **Step 4: Run the full image test file**

Run: `uv run pytest tests/test_image_claude_code_groundwork.py -m "not docker" -q`
Expected: all PASS — in particular the pre-existing `test_plan_disabled_is_the_empty_plan` and `test_control_argv_and_env_are_the_shipped_official` (control payloads carry no `system_prompt_extra`, so they are untouched).

- [ ] **Step 5: `make verify`, commit**

```bash
make verify
git add images/reference/claude-code-groundwork/agent.py tests/test_image_claude_code_groundwork.py
git commit -m "feat(images): policy_pointer prompt-only treatment via system_prompt_extra payload key"
```

---

### Task 5: Authoring kit — register both workflows, arm-name suffixes, agent parity

**Files:**
- Modify: `scripts/flagship/author_consistency.py` (`GROUNDED_PAYLOADS_BY_WORKFLOW`, new `ARM_SUFFIX_BY_WORKFLOW`, arm naming in `author_consistency` at line 297)
- Test: `tests/test_flagship_consistency_kit.py`

**Interfaces:**
- Consumes: Task 3/4's payload shapes; the kit's existing `grounded_payload_for`, `derive_tier`, 2-arm builder.
- Produces: `--workflow placebo_gate` and `--workflow policy_pointer` author 2-arm experiments with treatment arm names `<tier>-placebo` / `<tier>-pointer` (historical rungs keep `<tier>-grounded` byte-identically). Tasks 6/8 invoke these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_flagship_consistency_kit.py`:

```python
# --------------------------------------------------------------------------- #
# mechanism-decomposition workflows [design: docs/design/
# mechanism-decomposition-program.md]: placebo_gate + policy_pointer
# --------------------------------------------------------------------------- #

def test_mechanism_decomposition_payloads_exact():
    assert ac.GROUNDED_PAYLOADS_BY_WORKFLOW["placebo_gate"] == {
        "tools": ["groundwork"], "workflow": "placebo_gate"}
    assert ac.GROUNDED_PAYLOADS_BY_WORKFLOW["policy_pointer"] == {
        "system_prompt_extra": "policy_pointer"}


def test_treatment_arm_suffix_per_workflow(corpus_out: Path, tmp_path: Path):
    # historical rungs keep <tier>-grounded byte-identically; the new
    # treatments get honest names — an arm labeled "grounded" that stages no
    # tool (pointer) would be a mislabeled condition.
    cases = {"ground_verify": "haiku-grounded", "placebo_gate": "haiku-placebo",
             "policy_pointer": "haiku-pointer"}
    for wf, arm in cases.items():
        r = ac.author_consistency(
            corpus_out, tmp_path / f"exp-{wf}", trial_image="sha256:t",
            workflow=wf, reps=1, ceiling=35.0, quiet=True, tasks=["gw-r5"])
        assert r.grounded_arm == arm, wf
        assert r.bare_arm == "haiku-bare", wf


def test_kit_payloads_are_armable_by_the_trial_agent():
    """Parity fence: every payload the kit can author must be a payload the
    trial image's plan_groundwork accepts — a kit entry the agent refuses
    would fail every treated trial mid-run, after real spend on the bare arm."""
    import importlib.util

    img = _REPO / "images" / "reference" / "claude-code-groundwork" / "agent.py"
    base = _REPO / "images" / "base"
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    spec = importlib.util.spec_from_file_location("_kit_parity_agent", img)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_kit_parity_agent"] = mod
    spec.loader.exec_module(mod)
    for wf, payload in ac.GROUNDED_PAYLOADS_BY_WORKFLOW.items():
        mod.plan_groundwork(dict(payload), home="/h", workspace="/w")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_flagship_consistency_kit.py -k "mechanism or suffix or armable" -q`
Expected: first two FAIL (`KeyError: 'placebo_gate'` / refusal on unknown rung); the parity test PASSES over today's three rungs (keep it — it becomes the fence for the new entries).

- [ ] **Step 3: Implement in `author_consistency.py`**

Extend the payload table (`author_consistency.py:117-121`):

```python
GROUNDED_PAYLOADS_BY_WORKFLOW: dict[str, dict] = {
    "availability": {"tools": ["groundwork"]},
    "ground_verify": {"tools": ["groundwork"], "workflow": "ground_verify"},
    "ground_verify_enforced": {"tools": ["groundwork"], "workflow": "ground_verify_enforced"},
    # mechanism-decomposition treatments [design:
    # docs/design/mechanism-decomposition-program.md]: the placebo carries
    # rung 3's payload shape with the placebo workflow (the trial image swaps
    # the hook); the pointer is PROMPT-ONLY — no tools key at all (the image's
    # system_prompt_extra arming path; combining would be refused by the agent).
    "placebo_gate": {"tools": ["groundwork"], "workflow": "placebo_gate"},
    "policy_pointer": {"system_prompt_extra": "policy_pointer"},
}

# The treatment arm's name suffix per workflow. The three historical rungs stay
# "<tier>-grounded" BYTE-IDENTICALLY (re-authoring a historical experiment must
# reproduce it); the new treatments get honest names — an arm labeled
# "grounded" that stages no tool would be a mislabeled condition in every
# ledger event and report.
ARM_SUFFIX_BY_WORKFLOW: dict[str, str] = {
    "availability": "grounded",
    "ground_verify": "grounded",
    "ground_verify_enforced": "grounded",
    "placebo_gate": "placebo",
    "policy_pointer": "pointer",
}
```

In `author_consistency` (line 297), replace the arm naming:

```python
    grounded_arm = f"{tier}-{ARM_SUFFIX_BY_WORKFLOW[workflow]}"
    bare_arm = f"{tier}-bare"
```
(`grounded_payload_for(workflow)` on line 295 already refuses unknown rungs before this line, so the suffix lookup cannot KeyError first — keep that ordering.)

Update the module docstring's rung list (two sentences for the new workflows) and the `--workflow` argparse help.

- [ ] **Step 4: Run the kit test file**

Run: `uv run pytest tests/test_flagship_consistency_kit.py -q`
Expected: all PASS — including the pre-existing byte-determinism and `test_rung_payloads_exact` tests (historical rungs unchanged).

- [ ] **Step 5: `make verify`, commit**

```bash
make verify
git add scripts/flagship/author_consistency.py tests/test_flagship_consistency_kit.py
git commit -m "feat(flagship): register placebo_gate + policy_pointer workflows with honest arm names"
```

---

### Task 6: md-placebo experiment (OPERATOR) — then STOP for the human checkpoint

Confirmatory placebo probe: gw-r5, `bare` vs `placebo_gate`, 12 reps each. Requires `.env` keys, docker, harbor. Cost ceiling $8 (projection ≈ 24 trials × haiku estimate; the run's own telemetry supersedes).

**Files:**
- Create: `runs/consistency/md-placebo/PRE-REGISTRATION.md` (before `bench plan` — Task 1 hashes it into the lock)
- No repo code changes.

- [ ] **Step 1: Rebuild + pin the trial image** (agent.py changed in Tasks 3–4)

```bash
docker build -t claude-code-groundwork:pinned11 images/reference/claude-code-groundwork
docker inspect -f '{{.Id}}' claude-code-groundwork:pinned11
```
Record the `sha256:…` id; it is `<TRIAL_IMAGE>` below. Sanity: `uv run pytest tests/test_image_claude_code_groundwork.py -m docker -q` (the docker-marked smoke) if the daemon is available.

- [ ] **Step 2: Write the pre-registration** — create `runs/consistency/md-placebo/PRE-REGISTRATION.md` with exactly this content (dates/actor filled at write time):

```markdown
# md-placebo — pre-registration (mechanism decomposition, piece 1)

Design of record: docs/design/mechanism-decomposition-program.md (human-approved
2026-07-09). Locked before any md-placebo trial ran; hashed into the lock event.

## Question
The consistency program's r5 rescue (bare 0/17 -> enforced 16/17) bundles the
blocking forcing-function with the gate's findings content. Which does the work?

## Design
gw-r5 only; arms haiku-bare vs haiku-placebo (payload
{"tools":["groundwork"],"workflow":"placebo_gate"}); 12 reps per arm; model
anthropic/claude-haiku-4-5-20251001; trial image <TRIAL_IMAGE>; ceiling $8.
The placebo arm is byte-identical to rung-3 enforced (tools staged, rung-2
prompt token, 3-round fail-open Stop hook, enforce log) EXCEPT the hook runs no
gate and blocks each Stop with the static text "Review your changes for policy
compliance before finishing."

## Primary endpoint
holdout_pass_rate on gw-r5 (the fused score, decomposed post-hoc by
scripts/flagship/decompose_scores.py into functional/gate channels).

## Bound readings (frozen now)
- placebo <= 2/12: the gate's FINDINGS CONTENT is the active ingredient; the
  enforcement claim gains mechanism attribution.
- placebo >= 9/12: generic forced re-review suffices; the consistency program's
  headline must be rewritten (the map's content is not doing the work on r5).
- 3-8/12: both ingredients contribute; report the split; no headline change in
  either direction without a follow-up.
Comparators: in-experiment paired bare (primary); historical anchors bare-haiku
r5 0/32 program-wide, enforced-haiku r5 16/17.

## Secondary endpoints
Rounds-to-block/clean distribution from groundwork-enforce.jsonl; cost premium
vs bare (native telemetry); harm check — placebo must not break the feature
tests bare passes (100% of historical bare r5 failures were gate-only).

## Publish-the-null
Every outcome above is reportable; this file is committed to the lock hash.
```

- [ ] **Step 3: Author, lock, run, grade, attest, verify**

```bash
uv run python scripts/flagship/author_consistency.py \
  --corpus-out scratch/groundwork-v0/expt --out runs/consistency/md-placebo \
  --workflow placebo_gate --tasks gw-r5 --reps 12 --ceiling 8 \
  --trial-image <TRIAL_IMAGE>
uv run bench plan runs/consistency/md-placebo/experiment.yaml \
  --ledger runs/consistency/md-placebo/ledger.ndjson --actor <you>
uv run --env-file .env bench contamination probe runs/consistency/md-placebo \
  --manifest scratch/groundwork-v0/corpus-manifest.json --actor <you>
uv run --env-file .env bench run runs/consistency/md-placebo --engine harbor \
  --corpus-manifest scratch/groundwork-v0/corpus-manifest.json --actor <you>
VERDI_GRADER_IMAGE=<pinned grader digest> uv run bench grade runs/consistency/md-placebo \
  --runner docker --actor <you>
uv run python scripts/flagship/attest_models.py runs/consistency/md-placebo
uv run bench verify-chain runs/consistency/md-placebo/ledger.ndjson
```
Note: the authoring step must run AFTER the prereg file is written only in the sense that `bench plan` (the lock) needs the file present — `author_consistency` writes `experiment.yaml` into the same dir and does not touch `PRE-REGISTRATION.md`. Verify the lock event carries `prereg_sha256` (`grep prereg_sha256 runs/consistency/md-placebo/ledger.ndjson`).

- [ ] **Step 4: Decompose + verify treatment integrity**

```bash
VERDI_GRADER_IMAGE=<pinned grader digest> uv run python scripts/flagship/decompose_scores.py \
  runs/consistency/md-placebo --out runs/consistency/md-placebo
```
Also check, per placebo trial, `workspaces/trial-*/artifacts/groundwork-enforce.jsonl` exists and every `decision: block` line carries the static reason (treatment happened — the manipulation check).

- [ ] **Step 5: STOP — human checkpoint (mandatory)**

Report to the human: per-arm pass counts, the bound reading triggered, block/round distributions, decomposed channels, cost. **Do not author md-pointer or md-debait experiments before the human responds** — if the placebo reproduced the rescue, their framing changes (design doc, "stop-and-reassess gate"). Tasks 7–8's *code* may proceed; their *experiment runs* may not.

---

### Task 7: `gw-r5b` de-baited corpus task + corpus version bump

**Files:**
- Create: `corpora/groundwork-v0/tasks/gw-r5b/` (copy of `gw-r5`, prompt de-baited, id updated)
- Modify: `scripts/flagship/author_consistency.py:96-101` (`EXPECTED_TASK_IDS` + counts in messages), `:345` (`.corpus("groundwork-v0", "0.0.0")` → `"0.1.0"`)
- Modify: `tests/test_corpus_groundwork_v0.py:77` (inventory), `tests/test_flagship_consistency_kit.py` (`_CORPUS_TASKS`, any hardcoded 16s)
- Test: the two files above (updated expectations are the tests)

**Test change register (pre-approved by the design doc; restate in the commit):**
- `test_inventory_16_tasks_expected_classes` → 17 tasks, reach-trap 5→6 (intent preserved: pin the exact inventory; the inventory deliberately grew).
- `_CORPUS_TASKS` fixture in the kit tests gains `("gw-r5b", "reach-trap")`.
- Kit tests hardcoding 16 (`test_recon_projection_is_16xrepsx2xhaiku`, `test_tasks_omitted_authors_all_16_like_explicit_full_set`) → derive from `len(ac.EXPECTED_TASK_IDS)` or update to 17 (intent preserved: whole-corpus authoring, no silent subsetting).

- [ ] **Step 1: Update the corpus inventory test first (reproduce-first: it must fail against today's 16-task tree)**

In `tests/test_corpus_groundwork_v0.py`, update the inventory expectation to 17 tasks with `gw-r5b` in class `reach-trap` (edit the exact structure found at `test_inventory_16_tasks_expected_classes`, `tests/test_corpus_groundwork_v0.py:77` — keep its assertion style, add the id and bump counts).

Run: `uv run pytest tests/test_corpus_groundwork_v0.py::test_inventory_16_tasks_expected_classes -q`
Expected: FAIL (gw-r5b missing).

- [ ] **Step 2: Create the task**

```bash
cp -R corpora/groundwork-v0/tasks/gw-r5 corpora/groundwork-v0/tasks/gw-r5b
```
Edit `corpora/groundwork-v0/tasks/gw-r5b/task.meta.json`: change only `"id": "gw-r5"` → `"id": "gw-r5b"` (every other key byte-identical to gw-r5 — same seed, same trap, same substrate, same holdout description, same timeout).

Replace `corpora/groundwork-v0/tasks/gw-r5b/prompt.md` with exactly:

```markdown
# Audit sent messages

`mailsvc` is a small HTTP service that sends messages:

    POST /send    stores (sends) a message, returning 202 Accepted

Add a send audit: whenever a message is sent, record an audit entry for it. The
repository already exposes an audit method for this. A successful `POST /send`
still stores the message and returns promptly with `202 Accepted`.
```
(The ONLY change vs gw-r5: the bait clause "Auditing is bookkeeping and must not block or delay the send — a" is dropped; the sentence continues from "A successful…". Everything else byte-identical.)

Prepend one line to `corpora/groundwork-v0/tasks/gw-r5b/expected.md` (authoring documentation, never agent-visible): `> De-baited variant of gw-r5: identical service, policy, solution, and holdout; the prompt omits the async-steering sentence [design: docs/design/mechanism-decomposition-program.md, piece 3].` If `expected.md` itself restates the bait clause, neutralize it the same way as prompt.md.

- [ ] **Step 3: Extend the kit's expected set + version**

In `scripts/flagship/author_consistency.py`, `EXPECTED_TASK_IDS` reach-trap line becomes:

```python
    "gw-r1", "gw-r2", "gw-r3", "gw-r4", "gw-r5", "gw-r5b",  # reach-trap (r5b: de-baited r5)
```
…and update the comment above it (`5 reach` → `6 reach`, `= 16` → `= 17`) plus every user-facing "16" in this module's docstring/help strings (grep `16` in the file). Change line 345 to `.corpus("groundwork-v0", "0.1.0")` with a one-line comment: `# corpus version 0.1.0: gw-r5b added [design: mechanism-decomposition piece 3]`.

In `tests/test_flagship_consistency_kit.py`: add `("gw-r5b", "reach-trap")` to `_CORPUS_TASKS`; update the hardcoded-16 tests per the register above.

- [ ] **Step 4: Rebuild the corpus with the real binaries (OPERATOR — needs `go` + pinned `flowmap`/`groundwork`; `GROUNDWORK_REF=v0.0.0-20260707142329-7e8df2bb315a`)**

```bash
python3 corpora/groundwork-v0/build_tasks.py --check     # 17-task validation matrix, exit 0
python3 corpora/groundwork-v0/build_tasks.py --out scratch/groundwork-v0/expt
```
(Use `make corpus-groundwork-v0` instead if the Makefile defines it — the kit's refusal message names that target.) gw-r5b's committed `--check` row must show the same exemplar verdict as gw-r5 (`exemplar_expected_verify_rc: 1`) — identical workspace/policy, so identical graphs. Historical experiments keep their own holdout copies; the rebuild does not touch `runs/`.

- [ ] **Step 5: Run all affected tests**

Run: `uv run pytest tests/test_corpus_groundwork_v0.py tests/test_flagship_consistency_kit.py -q`
Expected: all PASS (inventory 17; kit refuses a 16-task corpus now — which is correct: the fixture provides 17).

- [ ] **Step 6: `make verify`, commit**

```bash
make verify
git add corpora/groundwork-v0/tasks/gw-r5b scripts/flagship/author_consistency.py \
        tests/test_corpus_groundwork_v0.py tests/test_flagship_consistency_kit.py
git commit -m "feat(corpus): gw-r5b de-baited r5 variant; corpus 0.1.0 (17 tasks)"
```

---

### Task 8: md-pointer + md-debait experiments (OPERATOR — only after the Task 6 human checkpoint clears)

Both exploratory, 5 reps, haiku, same lifecycle as Task 6. Write each `PRE-REGISTRATION.md` before `bench plan`.

- [ ] **Step 1: md-pointer** — create `runs/consistency/md-pointer/PRE-REGISTRATION.md`:

```markdown
# md-pointer — pre-registration (mechanism decomposition, piece 2)

Design of record: docs/design/mechanism-decomposition-program.md. Exploratory.

Question: does merely POINTING the agent at the in-repo policy rescue the
gate-discriminated traps? Treatment: one appended system-prompt line ("This
repository declares structural policy in `policy.json`; your change must honor
it.") — no tools, no hook. Tasks gw-r2 + gw-r5 (0% bare at haiku); arms
haiku-bare vs haiku-pointer; 5 reps; ceiling $6; model
anthropic/claude-haiku-4-5-20251001; trial image <TRIAL_IMAGE>.

Bound readings: expected NULL (instructed-rung agents saw BLOCK verdicts
in-session and shipped anyway) -> strengthens the forcing-function story. Any
material rescue (>=3/10 pooled where bare is 0) -> the enforcement stack is
overkill for the demonstrated effect; a confirmatory would be required before
citing. Exploratory either way; publish-the-null.
```

Then:

```bash
uv run python scripts/flagship/author_consistency.py \
  --corpus-out scratch/groundwork-v0/expt --out runs/consistency/md-pointer \
  --workflow policy_pointer --tasks gw-r2,gw-r5 --reps 5 --ceiling 6 \
  --trial-image <TRIAL_IMAGE>
```
…followed by the same plan/probe/run/grade/attest/verify-chain/decompose sequence as Task 6 Step 3–4 with `md-placebo` → `md-pointer`.

- [ ] **Step 2: md-debait** — create `runs/consistency/md-debait/PRE-REGISTRATION.md`:

```markdown
# md-debait — pre-registration (mechanism decomposition, piece 3)

Design of record: docs/design/mechanism-decomposition-program.md. Exploratory.

Question: how much of bare-haiku's 0/32 on gw-r5 is the prompt's bait
("must not block or delay the send")? Task gw-r5b (byte-identical to gw-r5
minus that clause; corpus 0.1.0); arms haiku-bare vs haiku-grounded
(workflow ground_verify_enforced); 5 reps; ceiling $6; model
anthropic/claude-haiku-4-5-20251001; trial image <TRIAL_IMAGE>.

Endpoints: (a) bare violation rate without the bait = the un-steered violation
propensity estimate (scope qualifier for the walkthrough's external-validity
section); (b) the enforced arm's rescue rate on a neutral prompt. Neither
changes the headline; both bound its generality. Publish-the-null.
```

Then:

```bash
uv run python scripts/flagship/author_consistency.py \
  --corpus-out scratch/groundwork-v0/expt --out runs/consistency/md-debait \
  --workflow ground_verify_enforced --tasks gw-r5b --reps 5 --ceiling 6 \
  --trial-image <TRIAL_IMAGE>
```
…followed by the same plan/probe/run/grade/attest/verify-chain/decompose sequence.

- [ ] **Step 3: Verify treatment integrity per experiment**

md-pointer: per treated trial, the flight-recorder session shows the appended system prompt and NO `mcp__groundwork` tools announced; `groundwork-mcp.jsonl` must not exist. md-debait: enforce log present; verify per-trial `--model` attestation is 100% haiku (`attest_models.py` exit 0).

---

### Task 9: Program addendum report

**Files:**
- Create: `runs/consistency/MD-REPORT.md` (the program's outcome record; `runs/` is gitignored — the report is an artifact beside the ledgers, like `REPORT.md` before it)

- [ ] **Step 1:** Write the addendum after all grading completes: per experiment — design, prereg hash (from the lock event), toplines per arm, the bound reading triggered, decomposed functional/gate channels, block/round anatomy, costs vs ceilings, attestation, chain status; a "judgment calls made without asking" section; explicit nulls. Follow the bound readings verbatim — the prereg text decides the interpretation, not the numbers' appeal.
- [ ] **Step 2:** Re-run `decompose_scores.py` across all ten experiments (seven historical + three new) so `DECOMPOSITION.md` is program-complete.
- [ ] **Step 3:** Report to the human with the addendum and the updated decomposition; propose (do not make) any WALKTHROUGH.md update.

---

## Self-review notes (spec coverage)

- Design piece 0 (decompose) → Task 2. Piece 1 (placebo) → Tasks 3, 5, 6. Piece 2 (pointer) → Tasks 4, 5, 8. Piece 3 (de-bait) → Tasks 7, 8. Rider → Task 1. Human checkpoint → Task 6 Step 5 (hard stop). Program addendum → Task 9.
- The placebo hook blocks every Stop until exhaustion (design: "blocks each Stop (until round exhaustion)") — implemented exactly so in `PLACEBO_HOOK_PY`.
- Arm names: design says 2-arm bare-vs-treatment throughout; the kit's `<tier>-placebo`/`<tier>-pointer` suffixes are a naming honesty addition consistent with the design's "fail-closed posture preserved" and flagged to the human in the plan summary.
- Types are consistent across tasks: `PLACEBO_HOOK_PY: str`, `SYSTEM_PROMPT_EXTRAS: dict[str, str]`, `split_holdout_argv(list[str]) -> tuple[list[str], list[str]]`, `commit_prereg(spec_path) -> Optional[str]`.
