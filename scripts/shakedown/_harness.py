"""Portable helpers for the shakedown acceptance scripts.

After the SDK + images + hermetic conversions (refactor 08 §1, Phase 3D) EVERY
shakedown script — the hermetic golden/official/tripwires and the real-container
harbor/harbor_multiagent — authors + drives experiments in-process through
``harness.sdk`` (builders + ``ExperimentWorkspace``), builds images through
``harness.images``, and meters egress through the managed proxy: no hand-built
spec dicts, no ``inject_grades``, no raw ``docker`` calls, and reads go through
``LedgerView``. So what stays here is genuinely script-local: the ``Tally``,
``_run/`` staging, and the one ``bench`` subprocess driver kept for the vectors
whose *point* is the installed console script (the pre-registration refusals
exercise the CLI's refusal→exit-code mapping).

``dump_yaml`` stays load-bearing for the hermetic suite (``tripwires.py`` emits
deliberately-invalid specs the validating SDK builder would refuse). Scripts
import ``harness.*`` freely but never ``tests.*``.

This module is script-local *plumbing* — the ``Tally``, ``_run/`` staging, the
``bench`` console-script driver, ANSI stripping, key gating, and layer banners.
The shared known-answer scenario *content* (the golden experiment shape and the
harbor helpers) lives in ``_scenario.py``.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from harness.sdk import MissingEnvKeysError, require_env_keys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
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


def empty_dir(name: str) -> Path:
    d = RUN / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def dump_yaml(path, data):
    Path(path).write_text(yaml.safe_dump(data), encoding="utf-8")


def banner(title: str) -> None:
    """Print a layer header: a rule, the title on its own line, then a rule."""
    rule = "=" * 72
    print(f"{rule}\n{title}\n{rule}")


def require_keys_or_exit(*keys: str, script: str) -> None:
    """Gate on required env keys; on a miss, exit with the ``--env-file .env`` hint."""
    try:
        require_env_keys(*keys)
    except MissingEnvKeysError as e:
        raise SystemExit(f"{e}\nrun: uv run --env-file .env python scripts/shakedown/{script}")


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
