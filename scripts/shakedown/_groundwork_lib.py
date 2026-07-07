"""Shared groundwork-corpus plumbing for the pipeline smoke AND the flagship kit.

The verdi-go integration plan (§6 / §10 P3–P4) has TWO consumers of the same
corpus-authoring pattern: the keyless pipeline smoke
(``scripts/shakedown/groundwork_pipeline.py``, the P3 local exit gate) and the
flagship authoring kit (``scripts/flagship/author_pilot.py`` /
``author_flagship.py``, the P4 pre-registration). Rather than let the two copies
drift, the reusable pieces the smoke pioneered live here and both consumers import
them (owner directive: reuse over bespoke).

What lives here — the pieces with no flagship-specific policy:

* **corpus materialization** via ``corpora/groundwork-v0/build_tasks.py`` (the
  ``--out`` / ``--solutions`` emit) and the verdi-go toolchain resolver;
* **experiment authoring against this corpus** — reconstructing the emitted
  ``tasks.yaml`` entries into SDK :class:`~harness.sdk.Task` objects, the §6
  payload-asymmetry constants, and the two-arm builder;
* **seeded, class-covering task selection** (stratified subset + round-robin
  slice) the pilot needs and the smoke can reuse;
* **verb-driving / grading helpers** — the per-arm tree materialization, the
  redirecting local-exec grade runner, the discrimination table, and the funnel
  driver.

What stays OUT (genuinely flagship-bespoke, in ``scripts/flagship/``): the D4
decision table, the ``plan/power.py`` MDE consumption, and the cost model.

Determinism: every selection is a pure function of ``(task_dicts, seed,
class_order)`` — a seeded shuffle over a stable per-class key, no wall clock, no
map-iteration order. Imports ``harness.*`` freely (the SDK is a leaf); never
imports ``tests.*``. The binary/toolchain helpers import ``harness.grade.*``
lazily so this module loads without a Go toolchain (the hermetic flagship tests
import the pure helpers only).
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpora" / "groundwork-v0"
BUILDER = CORPUS / "build_tasks.py"
GRADER_BIN_DIR = REPO / "images" / "grader"
VERDI_GO = Path("/home/user/verdi-go")
FUNNEL_FIXTURE = REPO / "tests" / "fixtures" / "funnel" / "grounded_checked"
FUNNEL_TOOL = REPO / "scripts" / "funnel_metrics.py"

# --------------------------------------------------------------------------- #
# §6 arm shape — the one place the payload asymmetry + model ids are defined.
# --------------------------------------------------------------------------- #
# Grounded (arm_a — the paired delta is arm_a - arm_b) carries the payload
# asymmetry; bare is the control. Payloads per plan §6: {} vs the groundwork
# tool + ground_verify workflow. The arm model ids are the verdi-bench schema's
# fully-versioned convention (``anthropic/<model>-<date>``), matching the plan
# §6 sketch; the pilot/flagship reuse them so "which model" lives in one place.
MODEL_OPUS = "anthropic/claude-opus-4-8-20260101"
MODEL_HAIKU = "anthropic/claude-haiku-4-5-20251001"
GROUNDED_PAYLOAD = {"tools": ["groundwork"], "workflow": "ground_verify"}
BARE_PAYLOAD: dict = {}
GROUNDED, BARE = "grounded", "bare"

# The pilot's judge is a NEVER-INVOKED placeholder: grade-only calibration needs
# no judge (judging is idempotent and costs money), and judge_preference
# calibration belongs to the flagship (plan §6). The ``fake/`` vendor makes the
# "this does not run" intent unmistakable and validates through the schema (it is
# fully versioned, so it is not an alias). The flagship resolves the REAL judge —
# an OpenAI GPT-5.x id, passed via --judge-model at author_flagship time (D5).
PLACEHOLDER_JUDGE = "fake/deterministic-2026-01-01"

# Canonical corpus class order (task.meta.json `class` values). Round-robin
# selection walks this order so the smallest slice already samples a binding
# trap AND the non-binding null (trap, null, trap, trap): k=2 → {reach-trap,
# null}; k>=4 → every class. Any class not listed sorts after (fail-open, not a
# silent drop).
CLASS_ORDER = ("reach-trap", "null", "obligation-trap", "multi-impl")


# --------------------------------------------------------------------------- #
# toolchain (binary-dependent; smoke path only)
# --------------------------------------------------------------------------- #
def ensure_binaries(tally, scratch: Path) -> dict:
    """Resolve flowmap/groundwork from the env, else build them from the verdi-go
    tip (GOWORK=off), echo the build identity (the pin), and put the grader dir on
    PATH so ``verdi-groundwork-check`` resolves by bare name."""
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
    fv = subprocess.run([flow, "version"], capture_output=True, text=True).stdout.strip()
    gv = subprocess.run([grnd, "version"], capture_output=True, text=True).stdout.strip()
    gover = subprocess.run(["go", "version"], capture_output=True, text=True).stdout.strip()
    print(f"    PIN: {fv} | {gv} | {gover}")
    tally.check("toolchain pinned + self-reporting",
                fv.startswith("flowmap ") and gv.startswith("groundwork "), f"{fv} / {gv}")
    os.environ["PATH"] = f"{GRADER_BIN_DIR}{os.pathsep}{os.environ['PATH']}"
    # Shared GOCACHE outside the per-run scratch (a build cache is not experiment
    # state) so go test + the wrapper's flowmap reuse the cache across trials/re-runs.
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
# authoring against the corpus (pure; hermetic)
# --------------------------------------------------------------------------- #
def load_corpus_tasks(corpus_out: Path) -> list[dict]:
    """Read the emitted ``tasks.yaml`` into task dicts.

    Loaded with ``yaml.safe_load`` so it accepts both the ``build_tasks.py --out``
    emission (JSON, which is valid YAML) and an already-SDK-written ``tasks.yaml``
    (the lenient reader's own posture)."""
    import yaml

    doc = yaml.safe_load((Path(corpus_out) / "tasks.yaml").read_text(encoding="utf-8"))
    return list(doc["tasks"])


def task_to_sdk(d: dict, *, image: Optional[str] = None):
    """Reconstruct one emitted corpus task into an SDK :class:`Task` value object.

    The SDK ``TaskSpec`` validates each task on the way in — a free corpus
    cross-check (the shakedown-established pattern). ``image`` (a digest-pinned
    trial-image ref) is stamped on the task when given — the harbor arms all share
    ONE ``claude-code-groundwork`` image whose payload gates the tooling (plan §4);
    ``None`` leaves the fake-engine default (the hermetic/smoke path)."""
    from harness.sdk import Task

    return Task(
        id=d["id"], prompt=d.get("prompt", ""), image=image, task_class=d.get("task_class"),
        holdouts_dir=d.get("holdouts_dir"),
        holdout_canaries=tuple(d.get("holdout_canaries", [])),
        timeout_s=d.get("timeout_s"), plugin_ids=tuple(d.get("plugin_ids", [])),
        files=d.get("files"),
    )


def add_corpus_tasks(exp, task_dicts: list[dict], ids: Optional[list[str]] = None, *,
                     image: Optional[str] = None):
    """Append the selected corpus tasks (all, or the ``ids`` subset in ``ids``
    order) to an :class:`Experiment`, stamping ``image`` on each. Returns ``exp``."""
    if ids is None:
        for d in task_dicts:
            exp.task(task_to_sdk(d, image=image))
        return exp
    by_id = {d["id"]: d for d in task_dicts}
    for tid in ids:
        if tid not in by_id:
            raise KeyError(f"selected task {tid!r} not in the emitted corpus tasks.yaml")
        exp.task(task_to_sdk(by_id[tid], image=image))
    return exp


def build_two_arm(name: str, *, model: str, seed: int, ceiling: float, judge: str,
                  reps: int = 1, corpus: tuple[str, str] = ("groundwork-v0", "0.0.0"),
                  platform: str = "claude_code", grounded_arm: str = GROUNDED,
                  bare_arm: str = BARE):
    """The §6 2-arm shape (grounded vs bare, same model): the tool-effect A/B for
    one model tier. Grounded carries the payload asymmetry; bare is the control."""
    from harness.sdk import Experiment

    return (Experiment(name, seed=seed, cost_ceiling_usd=ceiling)
            .arm(grounded_arm, model=model, platform=platform, payload=dict(GROUNDED_PAYLOAD))
            .arm(bare_arm, model=model, platform=platform, payload=dict(BARE_PAYLOAD))
            .judge(judge)
            .corpus(*corpus)
            .repetitions(reps))


def copy_holdouts(corpus_out: Path, out_dir: Path, ids: list[str]) -> int:
    """Copy each selected task's ``holdouts/<id>/`` tree from the emitted corpus
    into ``out_dir/holdouts/<id>/`` so the authored experiment's ``holdouts_dir``
    (a path relative to the experiment dir) resolves. Deterministic clean copy."""
    src_root = Path(corpus_out) / "holdouts"
    dst_root = Path(out_dir) / "holdouts"
    n = 0
    for tid in ids:
        src = src_root / tid
        if not src.is_dir():
            raise FileNotFoundError(f"no holdouts/{tid} under {corpus_out}; run build_tasks.py --out first")
        dst = dst_root / tid
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        n += 1
    return n


def run_config(*, allowlist: list[str], keys_by_arm: dict[str, list[str]],
               log_path: str = "metering/verdi.jsonl") -> dict:
    """The managed-metering-proxy ``run.config.yaml`` mapping: the harness stands
    the proxy up + tears it down around the run and injects per-arm provider keys.

    ``allowlist`` must include both model-API hosts the flagship touches
    (api.anthropic.com for the claude_code arms, api.openai.com for the OpenAI
    judge — D5). ``keys_by_arm`` maps each arm to the ENV NAMES whose VALUES the
    run injects into that arm's trial container (never persisted). The JUDGE key
    is NOT here: the judge runs as a host process and reads OPENAI_API_KEY from
    the process env directly (see the runbook's key-handling note)."""
    return {
        "proxy": {"managed": True, "allowlist": list(allowlist), "log_path": log_path},
        "provider_key_names_by_arm": {a: list(v) for a, v in keys_by_arm.items()},
    }


# --------------------------------------------------------------------------- #
# seeded, class-covering task selection (pure; hermetic)
# --------------------------------------------------------------------------- #
def stratify_by_class(task_dicts: list[dict]) -> dict[str, list[str]]:
    """Group task ids by ``task_class`` (sorted ids within each class)."""
    buckets: dict[str, list[str]] = {}
    for d in sorted(task_dicts, key=lambda x: x["id"]):
        buckets.setdefault(str(d.get("task_class") or "unknown"), []).append(d["id"])
    return buckets


def _class_seed(seed: int, cls: str) -> int:
    """A stable per-class RNG seed — sha256 over ``seed:class`` so the draw does
    not depend on Python hash randomization (determinism across runs/interpreters)."""
    return int.from_bytes(hashlib.sha256(f"{seed}:{cls}".encode("utf-8")).digest()[:8], "big")


def select_stratified(task_dicts: list[dict], k: int, *, seed: int,
                      class_order: tuple[str, ...] = CLASS_ORDER) -> list[str]:
    """Select ``k`` task ids, round-robin across classes so coverage is maximal.

    Within each class the ids are drawn by a seeded shuffle keyed on
    ``(seed, class)``; the round-robin walks ``class_order`` (then any remaining
    classes, sorted) taking one per class per pass. So the first ``len(classes)``
    picks are one-per-class — for ``k >= #classes`` every class is covered, and for
    ``k < #classes`` the covered classes follow ``class_order``. Pure function of
    ``(task_dicts, k, seed, class_order)``; returned in round-robin (pick) order,
    which is itself deterministic. Raises if ``k`` exceeds the corpus size."""
    buckets = stratify_by_class(task_dicts)
    total = sum(len(v) for v in buckets.values())
    if k > total:
        raise ValueError(f"cannot select {k} tasks from a corpus of {total}")
    shuffled: dict[str, list[str]] = {}
    for cls, ids in buckets.items():
        xs = list(ids)
        random.Random(_class_seed(seed, cls)).shuffle(xs)
        shuffled[cls] = xs
    order = [c for c in class_order if c in shuffled] + sorted(c for c in shuffled if c not in class_order)
    idx = {c: 0 for c in order}
    picked: list[str] = []
    while len(picked) < k and any(idx[c] < len(shuffled[c]) for c in order):
        for c in order:
            if len(picked) >= k:
                break
            if idx[c] < len(shuffled[c]):
                picked.append(shuffled[c][idx[c]])
                idx[c] += 1
    return picked


def classes_of(task_dicts: list[dict], ids: list[str]) -> set[str]:
    """The set of ``task_class`` labels spanned by ``ids``."""
    by_id = {d["id"]: str(d.get("task_class") or "unknown") for d in task_dicts}
    return {by_id[i] for i in ids if i in by_id}


# --------------------------------------------------------------------------- #
# per-arm workspace materialization (binary-adjacent; smoke path)
# --------------------------------------------------------------------------- #
def materialize_tree(workspace: Path, src_tree: Path) -> None:
    """Replace the workspace's staged base tree with ``src_tree`` (solution or
    exemplar), preserving ``artifacts/``. A clean replace — no stale base file
    survives to confuse the regenerated graph."""
    for child in workspace.iterdir():
        if child.name == "artifacts":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for item in src_tree.iterdir():
        dst = workspace / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy2(item, dst)


def materialize_arms(ws, sols: Path, *, grounded_arm: str = GROUNDED) -> int:
    """Between run and grade, write each trial's arm-appropriate tree: grounded →
    reference ``solution/`` (clean), bare → ``exemplar-violation/`` (violating).
    The fake engine is arm-blind, so this is how the shakedown realizes the per-arm
    asymmetry — grading then regenerates the branch graph and runs the REAL gate."""
    n = 0
    for tv in ws.view().trials():
        rec = tv.record
        workspace = Path(rec["artifacts_path"]).parent
        if rec["arm"] == grounded_arm:
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
    """local-exec, exporting the documented off-container redirect vars per trial
    so the corpus command holdout + the ``verdi-groundwork-check`` wrapper
    regenerate the branch graph off-container (in the grade container these are
    unset). Owning the per-trial loop is required because batch ``grade`` sets ONE
    global env, which cannot serve a multi-task corpus whose holdouts differ."""
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
    then drives ``grade_trial`` per trial with the redirecting local-exec runner."""
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
# discrimination table + funnel (smoke path)
# --------------------------------------------------------------------------- #
def _is_null_task(task_id: str) -> bool:
    return task_id.startswith("gw-n")


def _verdict(grade: dict) -> str:
    for a in grade.get("assertions", []):
        if a.get("source") == "plugin:groundwork" and a.get("id") == "groundwork:verdict":
            return a.get("result", "?")
    return "-"


def discrimination_table(tally, ws, *, grounded_arm: str = GROUNDED, bare_arm: str = BARE) -> None:
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
        g_grounded = by_task[tid].get(grounded_arm, {})
        g_bare = by_task[tid].get(bare_arm, {})
        p_grounded = bool(g_grounded.get("binary_score"))
        p_bare = bool(g_bare.get("binary_score"))
        null = _is_null_task(tid)
        cls = "null" if null else "trap"
        if null:
            n_nulls += 1
            disc = p_grounded and p_bare
            ok_nulls += disc
        else:
            n_traps += 1
            disc = p_grounded and not p_bare
            ok_traps += disc
        gm = f"{'PASS' if p_grounded else 'FAIL'}[{_verdict(g_grounded)}]"
        bm = f"{'PASS' if p_bare else 'FAIL'}[{_verdict(g_bare)}]"
        print(f"    {tid:7} {cls:5} {gm:16} {bm:16} {'yes' if disc else 'NO'}")
    print("    " + "-" * 62)
    tally.check("trap tasks: solution PASS, exemplar FAIL (gate discriminates through the pipeline)",
                ok_traps == n_traps and n_traps > 0, f"{ok_traps}/{n_traps} trap tasks discriminate")
    tally.check("null tasks: both PASS (no false friction)",
                ok_nulls == n_nulls and n_nulls > 0, f"{ok_nulls}/{n_nulls} null tasks clean both arms")


def load_funnel_module():
    """Load ``scripts/funnel_metrics.py`` by path (the standalone-tool import
    pattern), so the funnel assertions do not depend on a namespace import."""
    spec = importlib.util.spec_from_file_location("funnel_metrics", FUNNEL_TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def plant_and_run_funnel(tally, ws, expt: Path, *, grounded_arm: str = GROUNDED) -> None:
    """Plant the committed synthetic funnel fixture into two GROUNDED trial artifact
    dirs (fake-engine trials produce no real MCP telemetry — this is the honest way
    to exercise the funnel end to end), then run the funnel tool over the dir."""
    planted = []
    for tv in ws.view().trials():
        rec = tv.record
        if rec["arm"] == grounded_arm and len(planted) < 2:
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
    fm = load_funnel_module()
    rows = {row["trial_id"]: row for row in fm.iter_experiment_trials(expt)}
    planted_ok = all(
        rows[p]["grounded_before_edit"] is True and rows[p]["checked_after_last_edit"] is True
        and rows[p]["verdict_heeded"] is True for p in planted
    )
    others_null = all(
        r["has_mcp_log"] is False and all(r[m] is None for m in fm.METRIC_IDS)
        for tid, r in rows.items() if tid not in planted
    )
    tally.check("funnel computes on planted treatment trials (grounded_before_edit / checked / heeded = True)",
                bool(planted) and planted_ok, f"{len(planted)} planted trial(s) heeded")
    tally.check("funnel telemetry_null discipline: no-log trials are null, never false",
                others_null, "control/un-planted trials → null (not applicable)")
