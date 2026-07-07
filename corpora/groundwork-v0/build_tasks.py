#!/usr/bin/env python3
"""Emit / validate the groundwork-v0 corpus (verdi-go integration plan Track A3).

This is a stdlib-only builder — it imports NO harness code (the corpus must stay
loadable without the verdi-bench package) and shells out to the pinned
``flowmap`` / ``groundwork`` binaries for every claim. It has three modes:

  --out <dir>       Emit an experiment-ready directory: ``tasks.yaml`` (the
                    write-side schema of harness/schema/tasks.py, emitted as JSON
                    which is valid YAML so the lenient reader loads it) plus
                    ``holdouts/<id>/`` carrying the real holdout.json contract
                    (harness/grade/holdouts.py) and the groundwork assets the
                    grader regenerates against.

  --solutions <dir> Emit the reference-solution tree per task, for the k=5 flake
                    baseline (harness/grade/baseline.py) admission step.

  --check           Re-run the (a)/(b)/(c) validation matrix per task with the
                    real binaries and print it. This is the corpus's reproducible
                    self-check; a non-zero exit means at least one cell is wrong.

Determinism: every directory walk is sorted, JSON is key-sorted, and no wall
clock / absolute host path is written into an emitted artifact.

Binaries are resolved from ``$VERDI_FLOWMAP_BIN`` / ``$VERDI_GROUNDWORK_BIN`` — the
SAME override the grader plugin and the ``verdi-groundwork-check`` wrapper honor —
else PATH; a set-but-missing override fails loud (never a silent wrong-build
fallback). ``$GO`` overrides the go toolchain (default ``go``).

The single-holdout reality (harness/grade/holdouts.py exposes exactly one
``holdout.json`` per task, a discriminated union — verified against the source)
means the plan's two conceptual checks (functional + groundwork gate) are
composed into ONE ``command`` holdout: ``go test ./...`` AND the groundwork gate,
so ``holdout_pass_rate`` = "shipped a working feature that respects the
invariant". Per-rule attribution rides the separate ``plugin_ids: [groundwork]``
vector. See README.md §"Holdout composition".
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS_DIR = HERE / "tasks"

# Binary discovery mirrors the rest of the toolchain — the grader plugin's
# ``harness/grade/plugins/groundwork_shell._resolve_binary`` AND the
# ``images/grader/verdi-groundwork-check`` wrapper: the ``VERDI_FLOWMAP_BIN`` /
# ``VERDI_GROUNDWORK_BIN`` override wins, else PATH. One env-var convention across
# the builder, the plugin, and the wrapper means a single ``export
# VERDI_FLOWMAP_BIN=…`` pins ALL of them to one build — the determinism the
# corpus's committed graphs depend on (a mixed toolchain is a mismatch groundwork
# will flag). ``$GO`` still overrides the go toolchain (default ``go`` on PATH).
FLOWMAP_BIN_ENV = "VERDI_FLOWMAP_BIN"
GROUNDWORK_BIN_ENV = "VERDI_GROUNDWORK_BIN"
FLOWMAP_DEFAULT = "flowmap"
GROUNDWORK_DEFAULT = "groundwork"
GO = os.environ.get("GO", "go")


def _resolve_binary(env_var: str, default_name: str) -> str:
    """The flowmap/groundwork binary: ``env_var`` override if set, else PATH.

    PARITY (named so it cannot drift): mirrors
    ``harness/grade/plugins/groundwork_shell._resolve_binary`` exactly —
    a set-but-missing override is a MISCONFIGURED PIN and fails LOUD rather than
    silently falling back to PATH and building with the wrong tool; an absent
    override falls back to PATH and fails loud if the tool is not found. Mirrored
    (not imported) because this builder stays stdlib-only (decision D3), and
    resolved at call time (like the harness copy) so importing this module needs
    no binaries — the hermetic corpus test can load it without a toolchain."""
    override = os.environ.get(env_var)
    if override:
        if not Path(override).is_file():
            raise SystemExit(
                f"{env_var}={override!r} does not point at a file; the groundwork "
                f"toolchain pin is misconfigured"
            )
        return override
    found = shutil.which(default_name)
    if not found:
        raise SystemExit(
            f"{default_name!r} not found on PATH and {env_var} is unset; set "
            f"{env_var} or install the pinned groundwork toolchain on PATH"
        )
    return found

# The grader-image wrapper the groundwork command holdout invokes, by BARE NAME so
# it resolves on PATH (portable): the grader image installs it at /usr/local/bin
# (on PATH), and a local/ADVISORY run puts images/grader/ on PATH — the old
# container-absolute path only existed in the grade container. It reads
# ${VERDI_HOLDOUTS_DIR:-/holdouts}/groundwork/{policy.json,base.graph.json}
# (the per-task holdouts dir, mounted AT /holdouts), regenerates the branch graph
# from the workspace with the policy's substrate, runs groundwork verify, and exits
# with groundwork's code. Shipped by the harness grader image (plan §3).
GROUNDWORK_WRAPPER = "verdi-groundwork-check"

# Files inside a workspace tree that are NOT staged to the agent or analyzed as
# task source. (Nothing today — kept explicit so a future stray file fails loud.)
_SKIP_NAMES = {".DS_Store"}


# --------------------------------------------------------------------------- #
# task model
# --------------------------------------------------------------------------- #
def discover_tasks() -> list[dict]:
    """Return every task (sorted by id) as a dict of its on-disk facts.

    ``substrate`` is read from the task's **policy.json** — the authoritative
    field the grader wrapper reads to pick ``flowmap --algo`` (a multi-impl
    task is unsound under rta: it false-blocks the clean solution) — and
    cross-checked against ``task.meta.json`` so the two cannot drift apart.
    """
    tasks = []
    for meta_path in sorted(TASKS_DIR.glob("*/task.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        tdir = meta_path.parent
        policy = json.loads((tdir / "workspace" / "policy.json").read_text(encoding="utf-8"))
        substrate = policy.get("substrate", "rta")
        if substrate != meta.get("graph_substrate", "rta"):
            raise SystemExit(
                f"{meta['id']}: policy.json substrate {substrate!r} disagrees with "
                f"task.meta.json graph_substrate {meta.get('graph_substrate')!r}"
            )
        tasks.append(
            {
                "id": meta["id"],
                "class": meta["class"],
                "meta": meta,
                "dir": tdir,
                "prompt": (tdir / "prompt.md").read_text(encoding="utf-8"),
                "substrate": substrate,
            }
        )
    if not tasks:
        raise SystemExit(f"no tasks found under {TASKS_DIR}")
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"duplicate task ids: {ids}")
    return tasks


def _walk_files(root: Path) -> list[Path]:
    """Sorted list of regular files under root (deterministic)."""
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name not in _SKIP_NAMES:
            out.append(p)
    return out


def workspace_files(task: dict) -> dict[str, str]:
    """The agent-visible workspace tree as {relpath: content}, sorted."""
    ws = task["dir"] / "workspace"
    files = {}
    for p in _walk_files(ws):
        files[p.relative_to(ws).as_posix()] = p.read_text(encoding="utf-8")
    return dict(sorted(files.items()))


def feature_tests(task: dict) -> list[tuple[str, str]]:
    """(relpath, content) for each hidden feature test — a ``*_test.go`` present
    in solution/ but absent from workspace/ (the acceptance test the base does
    not ship). Sorted by relpath. The two trees share it byte-for-byte."""
    ws = task["dir"] / "workspace"
    sol = task["dir"] / "solution"
    ws_tests = {p.relative_to(ws).as_posix() for p in _walk_files(ws) if p.name.endswith("_test.go")}
    out = []
    for p in _walk_files(sol):
        rel = p.relative_to(sol).as_posix()
        if p.name.endswith("_test.go") and rel not in ws_tests:
            out.append((rel, p.read_text(encoding="utf-8")))
    return sorted(out)


def task_canary(task_id: str) -> str:
    """A deterministic per-task canary token embedded only in holdout material,
    so a holdout-leak scan (AC-9) can assert it never reaches the trial."""
    return f"GWV0-{task_id}-HOLDOUT-CANARY"


# --------------------------------------------------------------------------- #
# binary shell-outs
# --------------------------------------------------------------------------- #
def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True)


def gen_graph(tree: Path, substrate: str, out_path: Path) -> None:
    """flowmap graph --algo <substrate> <tree> > out_path (fail loud)."""
    proc = _run([_resolve_binary(FLOWMAP_BIN_ENV, FLOWMAP_DEFAULT),
                 "graph", "--algo", substrate, str(tree)])
    if proc.returncode != 0:
        raise SystemExit(f"flowmap graph failed for {tree}:\n{proc.stderr}")
    out_path.write_text(proc.stdout, encoding="utf-8")


def groundwork_rc(argv: list[str]) -> tuple[int, str]:
    proc = _run([_resolve_binary(GROUNDWORK_BIN_ENV, GROUNDWORK_DEFAULT), *argv])
    return proc.returncode, (proc.stdout + proc.stderr)


def go_ok(tree: Path) -> tuple[bool, str]:
    """go build ./... && go vet ./... && go test ./... in tree."""
    for sub in (["build", "./..."], ["vet", "./..."], ["test", "./..."]):
        proc = _run([GO, *sub], cwd=tree)
        if proc.returncode != 0:
            return False, f"go {' '.join(sub)} failed:\n{proc.stdout}\n{proc.stderr}"
    return True, ""


# --------------------------------------------------------------------------- #
# --out : emit an experiment-ready directory
# --------------------------------------------------------------------------- #
def holdout_argv(task_id: str, tests: list[tuple[str, str]]) -> list[str]:
    """The composite ``command`` holdout: inject the hidden feature test(s),
    run the functional suite, then the groundwork gate. Exit 0 iff both pass.

    The holdouts root is resolved as ``${VERDI_HOLDOUTS_DIR:-/holdouts}`` — the
    SAME expression images/grader/verdi-groundwork-check uses (one source of truth
    for the per-task holdouts root). In the grade container VERDI_HOLDOUTS_DIR is
    unset, so it is the read-only ``/holdouts`` mount; off-container (the ADVISORY
    local-exec baseline) the harness points it at the per-task holdouts dir. The
    functional side files therefore live at ``$H/functional/…`` — NOT
    ``/holdouts/<id>/functional/…``: the per-task dir is mounted AT /holdouts, so
    the ``<id>`` segment was a path bug that made the cp — and thus the whole gate —
    fail in-container. ``go test`` runs, then the groundwork gate."""
    cps = "; ".join(
        f'cp "$H"/functional/{Path(rel).name} ./{rel}' for rel, _ in tests
    )
    script = (
        'set -e; H="${VERDI_HOLDOUTS_DIR:-/holdouts}"; '
        f"{cps}; {GO} test ./...; {GROUNDWORK_WRAPPER} {task_id}"
    )
    return ["sh", "-c", script]


def emit_out(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    holdouts_root = out_dir / "holdouts"
    tasks_yaml: list[dict] = []

    for task in discover_tasks():
        tid = task["id"]
        substrate = task["substrate"]
        tests = feature_tests(task)
        canary = task_canary(tid)

        # base graph (regenerated deterministically from workspace source)
        hgw = holdouts_root / tid / "groundwork"
        hgw.mkdir(parents=True, exist_ok=True)
        gen_graph(task["dir"] / "workspace", substrate, hgw / "base.graph.json")
        shutil.copyfile(task["dir"] / "workspace" / "policy.json", hgw / "policy.json")

        # functional side files (the hidden feature test), canary-stamped
        hfn = holdouts_root / tid / "functional"
        hfn.mkdir(parents=True, exist_ok=True)
        for rel, content in tests:
            stamped = content + f"\n// {canary}\n"
            (hfn / Path(rel).name).write_text(stamped, encoding="utf-8")

        # the single declared holdout (holdout.json v1)
        holdout = {
            "schema_version": 1,
            "kind": "command",
            "id": f"{tid}-functional-groundwork",
            "argv": holdout_argv(tid, tests),
        }
        (holdouts_root / tid / "holdout.json").write_text(
            json.dumps(holdout, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

        # agent-visible workspace = source + the freshly built base graph.json
        files = workspace_files(task)
        files["graph.json"] = (hgw / "base.graph.json").read_text(encoding="utf-8")

        tasks_yaml.append(
            {
                "id": tid,
                "prompt": task["prompt"],
                "timeout_s": task["meta"].get("timeout_s", 900),
                "task_class": task["class"],
                "plugin_ids": ["groundwork"],
                "holdouts_dir": f"holdouts/{tid}",
                "holdout_canaries": [canary],
                "files": dict(sorted(files.items())),
            }
        )

    # tasks.yaml as JSON (valid YAML; the lenient reader loads it unchanged)
    (out_dir / "tasks.yaml").write_text(
        json.dumps({"tasks": tasks_yaml}, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(f"emitted {len(tasks_yaml)} task(s) to {out_dir}")


# --------------------------------------------------------------------------- #
# --solutions : emit reference-solution trees (for the flake baseline)
# --------------------------------------------------------------------------- #
def freeze_graphs() -> None:
    """Write each task's committed ``workspace/graph.json`` (plan §2 asset) from
    its source with the task's substrate. Regenerate whenever the pinned flowmap
    binary changes; ``--check`` guards that the committed copy is not stale."""
    for task in discover_tasks():
        gen_graph(task["dir"] / "workspace", task["substrate"],
                  task["dir"] / "workspace" / "graph.json")
    print(f"froze graph.json for {len(discover_tasks())} task(s)")


def emit_solutions(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for task in discover_tasks():
        dst = out_dir / task["id"]
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(task["dir"] / "solution", dst)
        n += 1
    print(f"emitted {n} solution tree(s) to {out_dir}")


# --------------------------------------------------------------------------- #
# --check : reproduce the (a)/(b)/(c) validation matrix
# --------------------------------------------------------------------------- #
def check() -> int:
    import tempfile

    header = f"{'id':8} {'class':16} {'sub':4} {'go(w/s/e)':10} {'a.fit':6} {'b.sol':6} {'c.exm':6} rule"
    print(header)
    print("-" * len(header))
    failures = 0

    for task in discover_tasks():
        tid = task["id"]
        substrate = task["substrate"]
        pol = task["dir"] / "workspace" / "policy.json"
        want_c = task["meta"].get("exemplar_expected_verify_rc", 1)

        # the policy itself must load cleanly
        pc_rc, pc_out = groundwork_rc(["policy-check", str(pol)])
        if pc_rc != 0:
            failures += 1
            print(f"  {tid}: policy-check failed:\n{pc_out.strip()[:400]}")

        # the hidden acceptance test must exist and be byte-identical between
        # solution/ and exemplar-violation/ (functional-parity discipline)
        tests = feature_tests(task)
        if not tests:
            failures += 1
            print(f"  {tid}: no hidden feature test (solution adds no *_test.go)")
        for rel, content in tests:
            other = task["dir"] / "exemplar-violation" / rel
            if not other.exists() or other.read_text(encoding="utf-8") != content:
                failures += 1
                print(f"  {tid}: feature test {rel} missing/differs in exemplar-violation/")

        # go build/vet/test on all three trees
        go_marks = []
        for tree in ("workspace", "solution", "exemplar-violation"):
            ok, msg = go_ok(task["dir"] / tree)
            go_marks.append("P" if ok else "F")
            if not ok:
                failures += 1
                print(f"  {tid} {tree}: {msg.strip()[:400]}")

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            gen_graph(task["dir"] / "workspace", substrate, tdp / "b.json")
            gen_graph(task["dir"] / "solution", substrate, tdp / "s.json")
            gen_graph(task["dir"] / "exemplar-violation", substrate, tdp / "e.json")

            # committed workspace graph.json (if present) must match a fresh build
            committed = task["dir"] / "workspace" / "graph.json"
            if committed.exists() and committed.read_text() != (tdp / "b.json").read_text():
                failures += 1
                print(f"  {tid}: committed workspace/graph.json is STALE (regenerate)")

            a_rc, _ = groundwork_rc(["fitness", str(pol), str(tdp / "b.json")])
            b_rc, _ = groundwork_rc(["verify", str(pol), str(tdp / "b.json"), str(tdp / "s.json")])
            c_rc, c_out = groundwork_rc(["verify", str(pol), str(tdp / "b.json"), str(tdp / "e.json")])

        # extract the rule named in cell (c)
        rule = ""
        for line in c_out.splitlines():
            s = line.strip()
            if s.startswith("- ") and ("—" in s or ":" in s):
                rule = s[2:].split("—")[0].split(":")[0].strip() or s[2:60]
                break

        a_ok = a_rc == 0
        b_ok = b_rc == 0
        c_ok = c_rc == want_c
        if not (a_ok and b_ok and c_ok):
            failures += 1
        print(
            f"{tid:8} {task['class']:16} {substrate:4} {'/'.join(go_marks):10} "
            f"{('ok' if a_ok else 'FAIL'):6} {('ok' if b_ok else 'FAIL'):6} "
            f"{('ok' if c_ok else 'FAIL'):6} {rule if c_rc else '(clean)'}"
        )

    print("-" * len(header))
    print("ALL CELLS GREEN" if failures == 0 else f"{failures} FAILURE(S)")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="emit/validate the groundwork-v0 corpus")
    ap.add_argument("--out", type=Path, help="emit an experiment-ready directory")
    ap.add_argument("--solutions", type=Path, help="emit reference-solution trees")
    ap.add_argument("--freeze-graphs", action="store_true",
                    help="write committed workspace/graph.json per task")
    ap.add_argument("--check", action="store_true", help="run the validation matrix")
    args = ap.parse_args()

    did = False
    if args.freeze_graphs:
        freeze_graphs()
        did = True
    if args.out:
        emit_out(args.out)
        did = True
    if args.solutions:
        emit_solutions(args.solutions)
        did = True
    if args.check:
        return check()
    if not did:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
