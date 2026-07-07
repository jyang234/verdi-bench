"""Keyless end-to-end pipeline smoke over corpus groundwork-v0 (plan §6 / §10 P3, local half).

A deterministic, keyless, no-Docker drive of the FULL verdi-bench pipeline over
the groundwork-v0 corpus on the FAKE engine (not fully hermetic — it needs the Go
toolchain + the verdi-go source to build/run the real gate), grading through the
real ``flowmap`` / ``groundwork`` binaries via the local-exec ADVISORY tier —
the A-P2 recipe applied per trial. It proves the groundwork gate discriminates a
clean solution from an invariant-violating one *through the whole pipeline*
(plan→run→grade→judge→selfcheck→analyze→verify-chain), which is the P3 pilot's
local exit criterion. The harbor half (real models solving in real containers,
metered egress) is the OTHER half of P3 and needs keys+proxy+Docker.

Convention: this follows ``scripts/shakedown`` (``golden.py`` / ``tripwires.py``)
— a runnable script (NOT a pytest), authored + driven through ``harness.sdk``,
staging under ``_run/`` — not the hermetic acceptance suite. It is deliberately
NOT collected by pytest; the funnel utility it can exercise IS unit-tested
hermetically (``tests/test_funnel_metrics.py``).

    python scripts/shakedown/groundwork_pipeline.py [--plant-funnel-fixtures]

Binaries: ``VERDI_FLOWMAP_BIN`` / ``VERDI_GROUNDWORK_BIN`` if set (the corpus's
pinned toolchain), else built from the ``/home/user/verdi-go`` tip and the build
identity echoed. ``images/grader`` is put on PATH so ``verdi-groundwork-check``
resolves by bare name (the corpus's portable command holdout).

fake_behavior + per-arm asymmetry — the JUDGMENT CALL, stated plainly. The fake
engine is arm-BLIND: it reads only ``task.fake_behavior`` (a per-task field), so
"treatment gets the solution tree, control gets the exemplar-violation tree"
CANNOT be expressed through ``fake_behavior`` natively. The faithful, sanctioned
construction (the shipped SDK's own fake-path operator step — ``write_holdout_results``,
and the ``tripwires`` contamination vector's per-arm ``solution.py`` write) is to
materialize the arm's workspace tree BETWEEN run and grade. That is what this does:
grounded → the reference ``solution/`` tree, bare → the ``exemplar-violation/``
tree. Grading then regenerates the branch graph from that tree and runs the real
gate, so the PASS/FAIL is the gate's, not a scripted stub's.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Tally, banner, empty_dir  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpora" / "groundwork-v0"
BUILDER = CORPUS / "build_tasks.py"
GRADER_BIN_DIR = REPO / "images" / "grader"
VERDI_GO = Path("/home/user/verdi-go")
FUNNEL_FIXTURE = REPO / "tests" / "fixtures" / "funnel" / "grounded_checked"
FUNNEL_TOOL = REPO / "scripts" / "funnel_metrics.py"


def _load_funnel_module():
    """Load ``scripts/funnel_metrics.py`` by path (the standalone-tool import
    pattern ``tests/test_funnel_metrics.py`` uses), so the programmatic funnel
    assertions do not depend on a namespace-package import."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("funnel_metrics", FUNNEL_TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod

# §6 arm shape: grounded (arm_a — the paired delta is arm_a - arm_b) carries the
# payload asymmetry; bare is the control. Same model both arms — a clean
# tool-effect A/B (§6: "grounded-vs-bare per model tier"). Fake judge, seed,
# ceiling per §6's "fake engine + LocalGradeRunner shakedown first".
MODEL = "anthropic/claude-opus-4-8-20260101"
GROUNDED, BARE = "grounded", "bare"
FAKE_JUDGE = "fake/deterministic-2026-01-01"
SEED, CEILING = 1234, 100.0


# --------------------------------------------------------------------------- #
# toolchain
# --------------------------------------------------------------------------- #
def ensure_binaries(t: Tally, scratch: Path) -> dict:
    """Resolve flowmap/groundwork from the env, else build them from the verdi-go
    tip (GOWORK=off), and echo the build identity (the pin)."""
    flow = os.environ.get("VERDI_FLOWMAP_BIN")
    grnd = os.environ.get("VERDI_GROUNDWORK_BIN")
    if flow and grnd and Path(flow).is_file() and Path(grnd).is_file():
        print(f"    using pinned binaries from env:\n      flowmap={flow}\n      groundwork={grnd}")
    else:
        bindir = scratch / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        print(f"    VERDI_FLOWMAP_BIN/VERDI_GROUNDWORK_BIN unset — building from {VERDI_GO} tip ...")
        env = {**os.environ, "GOWORK": "off"}
        for name in ("flowmap", "groundwork"):
            r = subprocess.run(["go", "build", "-o", str(bindir / name), f"./cmd/{name}"],
                               cwd=str(VERDI_GO), env=env, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(f"go build {name} failed:\n{r.stderr}")
        flow, grnd = str(bindir / "flowmap"), str(bindir / "groundwork")
        os.environ["VERDI_FLOWMAP_BIN"], os.environ["VERDI_GROUNDWORK_BIN"] = flow, grnd
    # Echo the pin: the build identity is the git pseudo-version (compiler-independent),
    # so a locally-built binary can still MATCH the corpus's committed toolchain pin.
    fv = subprocess.run([flow, "version"], capture_output=True, text=True).stdout.strip()
    gv = subprocess.run([grnd, "version"], capture_output=True, text=True).stdout.strip()
    gover = subprocess.run(["go", "version"], capture_output=True, text=True).stdout.strip()
    print(f"    PIN: {fv} | {gv} | {gover}")
    t.check("toolchain pinned + self-reporting", fv.startswith("flowmap ") and gv.startswith("groundwork "),
            f"{fv} / {gv}")
    # verdi-groundwork-check resolves by bare name on PATH (the corpus's portable holdout).
    os.environ["PATH"] = f"{GRADER_BIN_DIR}{os.pathsep}{os.environ['PATH']}"
    # A shared GOCACHE so go test + the wrapper's flowmap reuse the build cache across
    # trials AND across re-runs. It lives OUTSIDE the per-run experiment scratch (a
    # build cache is not experiment state), so each run still gets a fresh scratch +
    # deterministic seed while re-runs stay fast; honors an external GOCACHE if set.
    os.environ.setdefault("GOCACHE", str(scratch.parent / ".gocache"))
    Path(os.environ["GOCACHE"]).mkdir(parents=True, exist_ok=True)
    return {"flowmap": fv, "groundwork": gv}


def build_corpus(scratch: Path) -> tuple[Path, Path]:
    """Materialize the corpus via the builder: ``--out`` (tasks.yaml + holdouts/,
    base graphs regenerated with THIS binary) and ``--solutions`` (reference trees)."""
    expt, sols = scratch / "expt", scratch / "solutions"
    for mode, dst in (("--out", expt), ("--solutions", sols)):
        r = subprocess.run([sys.executable, str(BUILDER), mode, str(dst)],
                           cwd=str(CORPUS), capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"build_tasks {mode} failed:\n{r.stdout}\n{r.stderr}")
        print(f"    {r.stdout.strip()}")
    return expt, sols


# --------------------------------------------------------------------------- #
# authoring — §6 spec over the emitted corpus tasks (SDK builder, the shakedown convention)
# --------------------------------------------------------------------------- #
def author_experiment(expt: Path):
    """Reconstruct the emitted corpus tasks into SDK ``Task`` value objects, wrap
    them in the §6 2-arm spec, and write experiment.yaml + tasks.yaml + rubric.md
    into ``expt`` (the builder's ``holdouts/`` stays). The SDK's ``TaskSpec``
    validates each task on the way in — a free corpus cross-check."""
    from harness.sdk import Experiment, Task

    tasks_doc = json.loads((expt / "tasks.yaml").read_text(encoding="utf-8"))
    exp = (Experiment("groundwork-v0-p3-smoke", seed=SEED, cost_ceiling_usd=CEILING)
           .arm(GROUNDED, model=MODEL, platform="claude_code",
                payload={"tools": ["groundwork"], "workflow": "ground_verify"})
           .arm(BARE, model=MODEL, platform="claude_code", payload={})
           .judge(FAKE_JUDGE)
           .corpus("groundwork-v0", "0.0.0")
           .repetitions(1))
    for d in tasks_doc["tasks"]:
        exp.task(Task(
            id=d["id"], prompt=d.get("prompt", ""), task_class=d.get("task_class"),
            holdouts_dir=d.get("holdouts_dir"),
            holdout_canaries=tuple(d.get("holdout_canaries", [])),
            timeout_s=d.get("timeout_s"), plugin_ids=tuple(d.get("plugin_ids", [])),
            files=d.get("files"),
        ))
    return exp.write(expt)


# --------------------------------------------------------------------------- #
# per-arm workspace materialization (the judgment-call construction)
# --------------------------------------------------------------------------- #
def materialize_tree(workspace: Path, src_tree: Path) -> None:
    """Replace the workspace's staged base tree with ``src_tree`` (solution or
    exemplar), preserving ``artifacts/`` (the engine's captured telemetry). A clean
    replace — not an overlay — so no stale base file survives to confuse the graph."""
    for child in workspace.iterdir():
        if child.name == "artifacts":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for item in src_tree.iterdir():
        dst = workspace / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy2(item, dst)


def materialize_arms(ws, sols: Path) -> int:
    """Between run and grade, write each trial's arm-appropriate tree: grounded →
    reference ``solution/`` (clean), bare → ``exemplar-violation/`` (violating).
    (The builder emits only solution trees; the exemplars are read from the
    read-only corpus source.)"""
    n = 0
    for tv in ws.view().trials():
        rec = tv.record
        workspace = Path(rec["artifacts_path"]).parent
        if rec["arm"] == GROUNDED:
            src = sols / rec["task_id"]
        else:
            src = CORPUS / "tasks" / rec["task_id"] / "exemplar-violation"
        materialize_tree(workspace, src)
        n += 1
    return n


# --------------------------------------------------------------------------- #
# grade — the local-exec ADVISORY tier, per-trial (the A-P2 recipe)
# --------------------------------------------------------------------------- #
def _redirecting_runner():
    """local-exec, but it exports the DOCUMENTED off-container redirect vars
    (VERDI_WORKSPACE_DIR / VERDI_HOLDOUTS_DIR) per trial from the workspace /
    holdouts it is already handed, so the corpus's command holdout + the
    ``verdi-groundwork-check`` wrapper regenerate the branch graph off-container.
    In the grade CONTAINER these are unset (the /workspace,/holdouts mounts apply);
    off-container the harness must set them per trial — exactly what
    ``bench corpus baseline --runner local-exec`` does by hand (corpus README
    "Admission"). Owning the per-trial loop (below) is required because the batch
    ``grade`` verb sets ONE global env for the whole run, which cannot serve a
    multi-task corpus whose holdouts differ per task."""
    from harness.grade.runners import LocalExecutingGradeRunner

    class _RedirectingLocalExec(LocalExecutingGradeRunner):
        def run_holdouts(self, cmd, workspace, holdouts_dir, nonce=None):
            os.environ["VERDI_WORKSPACE_DIR"] = str(workspace)
            os.environ["VERDI_HOLDOUTS_DIR"] = str(holdouts_dir)
            return super().run_holdouts(cmd, workspace, holdouts_dir, nonce)

    return _RedirectingLocalExec()


def grade_localexec(ws, expt: Path, actor: str = "shakedown") -> int:
    """Grade every trial through the real binaries + wrapper, per-trial. Mirrors
    ``grade_experiment``'s commitment preamble (lock + task-content commitment),
    then drives ``grade_trial`` per trial with the redirecting local-exec runner —
    so each grade is a real ``go test`` + ``groundwork verify`` (binary score) plus
    the ``groundwork`` plugin's per-rule vector."""
    from harness.corpus.commit import (assert_task_commitment, load_task_dicts,
                                        task_content_sha)
    from harness.grade.deterministic import grade_trial
    from harness.grade.runners import GradingContainer
    from harness.grade.types import GradeTask
    from harness.ledger.actor import resolve_actor
    from harness.ledger.events import EventContext
    from harness.plan.lock import assert_lock

    ledger = expt / "ledger.ndjson"
    lock = assert_lock(expt / "experiment.yaml", ledger)
    spec = lock.spec
    task_dicts = load_task_dicts(expt)
    assert_task_commitment(lock.event, task_dicts, corpus_id=spec.corpus.id, semver=spec.corpus.version)
    gtasks = {
        d["id"]: GradeTask(id=d["id"], task_sha=task_content_sha(d),
                           holdouts_dir=str(expt / (d.get("holdouts_dir") or "")),
                           plugin_ids=list(d.get("plugin_ids", [])))
        for d in task_dicts
    }
    ctx = EventContext(experiment_id=expt.name, actor=resolve_actor(actor))
    container = GradingContainer(runner=_redirecting_runner())
    n = 0
    for tv in ws.view().trials():
        rec = tv.record
        grade_trial(rec["trial_id"], gtasks[rec["task_id"]],
                    Path(rec["artifacts_path"]).parent, ledger, ctx,
                    container=container, fractional=spec.fractional_scoring)
        n += 1
    return n


# --------------------------------------------------------------------------- #
# discrimination table
# --------------------------------------------------------------------------- #
def _is_null_task(task_id: str) -> bool:
    return task_id.startswith("gw-n")


def _verdict(grade: dict) -> str:
    for a in grade.get("assertions", []):
        if a.get("source") == "plugin:groundwork" and a.get("id") == "groundwork:verdict":
            return a.get("result", "?")
    return "-"


def discrimination_table(t: Tally, ws) -> None:
    """Print the per-task PASS/FAIL table and assert the gate discriminates: trap
    tasks — grounded PASS, bare FAIL; null tasks — both PASS."""
    grades = ws.view().latest_grade_by_trial()
    by_task: dict[str, dict[str, dict]] = {}
    for tv in ws.view().trials():
        rec = tv.record
        g = grades.get(rec["trial_id"])
        if g is not None:
            by_task.setdefault(rec["task_id"], {})[rec["arm"]] = g

    print(f"\n    {'task':7} {'class':5} {'grounded(sol)':16} {'bare(exemplar)':16} discriminates?")
    print("    " + "-" * 62)
    ok_traps = ok_nulls = 0
    n_traps = n_nulls = 0
    for tid in sorted(by_task):
        g_grounded = by_task[tid].get(GROUNDED, {})
        g_bare = by_task[tid].get(BARE, {})
        p_grounded = bool(g_grounded.get("binary_score"))
        p_bare = bool(g_bare.get("binary_score"))
        null = _is_null_task(tid)
        cls = "null" if null else "trap"
        if null:
            n_nulls += 1
            disc = p_grounded and p_bare               # both PASS
            ok_nulls += disc
        else:
            n_traps += 1
            disc = p_grounded and not p_bare           # grounded PASS, bare FAIL
            ok_traps += disc
        gm = f"{'PASS' if p_grounded else 'FAIL'}[{_verdict(g_grounded)}]"
        bm = f"{'PASS' if p_bare else 'FAIL'}[{_verdict(g_bare)}]"
        print(f"    {tid:7} {cls:5} {gm:16} {bm:16} {'yes' if disc else 'NO'}")
    print("    " + "-" * 62)
    t.check("trap tasks: solution PASS, exemplar FAIL (gate discriminates through the pipeline)",
            ok_traps == n_traps and n_traps > 0, f"{ok_traps}/{n_traps} trap tasks discriminate")
    t.check("null tasks: both PASS (no false friction)",
            ok_nulls == n_nulls and n_nulls > 0, f"{ok_nulls}/{n_nulls} null tasks clean both arms")


# --------------------------------------------------------------------------- #
# funnel fixtures (planted, honestly labeled)
# --------------------------------------------------------------------------- #
def plant_and_run_funnel(t: Tally, ws, expt: Path) -> None:
    """Plant the committed synthetic funnel fixture (a real-``--log``-shaped
    groundwork-mcp.jsonl + v3 trajectory) into two GROUNDED trial artifact dirs —
    fake-engine trials produce no real MCP telemetry, so this is the honest way to
    exercise the funnel path end to end — then run the funnel tool over the
    experiment dir. The planted trials are labeled PLANTED in the output."""
    planted = []
    for tv in ws.view().trials():
        rec = tv.record
        if rec["arm"] == GROUNDED and len(planted) < 2:
            adir = Path(rec["artifacts_path"])
            adir.mkdir(parents=True, exist_ok=True)
            shutil.copy(FUNNEL_FIXTURE / "groundwork-mcp.jsonl", adir / "groundwork-mcp.jsonl")
            shutil.copy(FUNNEL_FIXTURE / "trajectory.json", adir / "trajectory.json")
            planted.append(rec["trial_id"])
    print(f"    PLANTED synthetic funnel fixtures into {len(planted)} grounded trial(s): {planted}")
    print("    (fake trials have NO real MCP telemetry — these JSONLs are synthetic, "
          "shaped on the real mcp.go --log emission)")

    r = subprocess.run([sys.executable, str(FUNNEL_TOOL), "--experiment", str(expt)],
                       capture_output=True, text=True)
    for line in (r.stdout or "").strip().splitlines():
        print("    " + line)
    if r.returncode != 0:
        print("    " + (r.stderr or "").strip())
    # the two planted grounded trials passed their gate (solution tree) → a surfaced
    # verdict that was heeded; every un-planted / control trial has no log → null.
    fm = _load_funnel_module()
    rows = {row["trial_id"]: row for row in fm.iter_experiment_trials(expt)}
    planted_ok = all(
        rows[p]["grounded_before_edit"] is True and rows[p]["checked_after_last_edit"] is True
        and rows[p]["verdict_heeded"] is True for p in planted
    )
    others_null = all(
        r["has_mcp_log"] is False and all(r[m] is None for m in fm.METRIC_IDS)
        for tid, r in rows.items() if tid not in planted
    )
    t.check("funnel computes on planted treatment trials (grounded_before_edit / checked / heeded = True)",
            bool(planted) and planted_ok, f"{len(planted)} planted trial(s) heeded")
    t.check("funnel telemetry_null discipline: no-log trials are null, never false",
            others_null, "control/un-planted trials → null (not applicable)")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plant-funnel-fixtures", action="store_true",
                    help="plant synthetic MCP/trajectory fixtures into 2 grounded trials and run the funnel tool")
    args = ap.parse_args()

    banner("groundwork-v0 pipeline smoke (fake engine, real gate, no keys/Docker) — plan §10 P3 local half")
    t = Tally("P3 local pipeline")
    scratch = empty_dir("groundwork_pipeline")

    print("[1/8] toolchain")
    ensure_binaries(t, scratch)
    print("[2/8] materialize corpus (builder --out / --solutions)")
    expt, sols = build_corpus(scratch)
    print("[3/8] author §6 2-arm experiment (grounded vs bare) + plan")
    ws = author_experiment(expt)
    ws.plan(actor="shakedown")
    locked = ws.view().by_kind("experiment_locked")
    t.check("experiment locked (§6-shaped: grounded/bare payload asymmetry)", len(locked) == 1,
            "one experiment_locked event")
    print("[4/8] run (fake engine)")
    ws.run(engine="fake")
    n_trials = len(ws.view().trials())
    print(f"    {n_trials} trials")
    print("[5/8] materialize per-arm trees (grounded→solution, bare→exemplar-violation)")
    n_mat = materialize_arms(ws, sols)
    print(f"    materialized {n_mat} trial workspaces")
    print("[6/8] grade (local-exec ADVISORY tier — real flowmap+groundwork+wrapper, per trial)")
    n_graded = grade_localexec(ws, expt)
    print(f"    graded {n_graded} trials")
    print("[7/8] judge → selfcheck → analyze --exploratory")
    ws.judge()
    ws.selfcheck(actor="shakedown")
    ws.analyze(exploratory=True)
    print("[8/8] verify-chain + artifact checks")
    chain = ws.verify_chain()

    discrimination_table(t, ws)

    # analyze --exploratory renders: findings.exploratory.md (the render), findings.json
    # (the machine-readable findings — mode-independent name), findings.exploratory.dossier.html.
    md = expt / "findings.exploratory.md"
    dossier = expt / "findings.exploratory.dossier.html"
    fjson = expt / "findings.json"
    t.check("ledger verify-chain green", chain.chain_ok, "chain OK")
    t.check("findings.exploratory.* rendered", md.exists() and fjson.exists(),
            "findings.exploratory.md + findings.json exist")
    t.check("dossier written", dossier.exists(), "findings.exploratory.dossier.html exists")

    if args.plant_funnel_fixtures:
        print("\n[funnel] --plant-funnel-fixtures")
        plant_and_run_funnel(t, ws, expt)

    print(f"\n    experiment dir: {expt}")
    print(f"    artifacts: experiment.yaml, tasks.yaml, ledger.ndjson, holdouts/, "
          f"findings.exploratory.(md|json|dossier.html)")
    t.finish()


if __name__ == "__main__":
    main()
