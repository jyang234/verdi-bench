"""Portable helpers for the shakedown acceptance scripts.

After the Phase-2 SDK conversion (refactor 08 §1) the hermetic scripts
(golden/official/tripwires) author + drive experiments in-process through
``harness.sdk`` — no hand-built spec dicts, no ``inject_grades``, and reads go
through ``LedgerView`` — so what stays here is genuinely script-local: the
``Tally``, ``_run/`` staging, and the one ``bench`` subprocess driver kept for
the vectors whose *point* is the installed console script (the pre-registration
refusals exercise the CLI's refusal→exit-code mapping).

The ``events``/``event_counts``/``dump_yaml``/``ASSETS`` helpers remain only
because the not-yet-converted harbor scripts (``harbor.py`` /
``harbor_multiagent.py``, which graduate in Phase 3) still import them; the
hermetic scripts no longer touch them. Scripts import ``harness.*`` freely but
never ``tests.*``.
"""
from __future__ import annotations

import re
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

# Strip terminal color so substring assertions on captured CLI output are stable
# under FORCE_COLOR (typer/rich emit SGR escapes that otherwise break matches) —
# the sanctioned fix for the known FORCE_COLOR shakedown flake (refactor 08 §1).
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s or "")


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
    """Invoke ``bench <args>``; echo the command + output tail (ANSI-stripped)."""
    cmd = [BENCH, *(str(a) for a in args)]
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=env)
    r.stdout, r.stderr = strip_ansi(r.stdout), strip_ansi(r.stderr)
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


def empty_dir(name: str) -> Path:
    d = RUN / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


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
