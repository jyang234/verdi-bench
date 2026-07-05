"""Portable helpers for the shakedown acceptance scripts.

Drives the installed ``bench`` console script (sibling to the venv python) as a
subprocess so every step exercises the real CLI, and reads the ledger through
``harness.ledger.query``. Generated run state goes under ``_run/`` (git-ignored);
committed inputs live under ``assets/``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ASSETS = HERE / "assets"
RUN = HERE / "_run"


def _bench_bin() -> str:
    for cand in (Path(sys.executable).parent / "bench", REPO / ".venv" / "bin" / "bench"):
        if cand.exists():
            return str(cand)
    found = shutil.which("bench")
    if found:
        return found
    raise SystemExit(
        "cannot find the `bench` console script; run `uv sync` then invoke via "
        "`uv run python scripts/shakedown/<script>.py`"
    )


BENCH = _bench_bin()


def bench(*args, check=True, env=None) -> subprocess.CompletedProcess:
    """Invoke ``bench <args>``; echo the command + output tail."""
    cmd = [BENCH, *(str(a) for a in args)]
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=env)
    print("$ bench " + " ".join(str(a) for a in args))
    for line in ((r.stdout or "") + (r.stderr or "")).strip().splitlines():
        print("    " + line)
    print(f"    -> exit {r.returncode}")
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED ({r.returncode}): bench {' '.join(str(a) for a in args)}")
    return r


def events(ledger, kind=None):
    from harness.ledger.query import find_events, read_events
    return read_events(Path(ledger)) if kind is None else find_events(Path(ledger), kind)


def event_counts(ledger) -> dict:
    return dict(sorted(Counter(e.get("event") for e in events(ledger)).items()))


def stage(name: str, template: str = "golden") -> Path:
    """Fresh ``_run/<name>`` seeded from ``assets/<template>``; returns the dir."""
    d = RUN / name
    if d.exists():
        shutil.rmtree(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ASSETS / template, d)
    return d


def empty_dir(name: str) -> Path:
    d = RUN / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def inject_grades(ledger, passes) -> int:
    """Write per-arm ``holdout_results.json`` into each trial workspace — the
    operator step the arm-blind fake engine needs for a decisive A/B."""
    n = 0
    for ev in events(ledger, "trial"):
        rec = ev["trial_record"]
        ws = Path(rec["artifacts_path"]).parent
        ws.mkdir(parents=True, exist_ok=True)
        result = "pass" if passes(rec["arm"], rec["task_id"]) else "fail"
        (ws / "holdout_results.json").write_text(
            json.dumps({"assertions": [{"id": "h1", "result": result}]}), encoding="utf-8")
        n += 1
    return n


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def dump_yaml(path, data):
    Path(path).write_text(yaml.safe_dump(data), encoding="utf-8")


class Tally:
    """Collects pass/fail results and prints a summary; exits nonzero on any fail."""

    def __init__(self, title):
        self.title = title
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        return ok

    def finish(self):
        npass = sum(1 for _, ok, _ in self.rows if ok)
        print(f"\n{self.title}: {npass}/{len(self.rows)} OK")
        failed = [n for n, ok, _ in self.rows if not ok]
        if failed:
            print("  FAILED: " + ", ".join(failed))
            raise SystemExit(1)
