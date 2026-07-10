"""Corpus admission properties for groundwork-v0 [integration plan §5, §10 P2].

Pins the leak / inventory / schema / portability invariants the P2 admission gate
depends on, so a regression in the stdlib-only builder
(``corpora/groundwork-v0/build_tasks.py``) or a stray holdout leak fails loudly
here rather than at render time.

Two layers, mirroring the corpus's own build/verify split:

* a FAST HERMETIC subset (no binaries — always runs under ``make verify``):
  inventory, task.meta schema + substrate agreement, the holdout-leak scan on the
  stdlib-only agent surface, and the emitted-holdout path portability; and
* a binary-gated tail SKIPPED unless ``VERDI_FLOWMAP_BIN`` (+ ``VERDI_GROUNDWORK_BIN``)
  is set — the builder's own ``--check`` matrix and a full-fidelity ``--out`` leak
  scan against the real ``tasks.yaml`` — so ``make verify`` stays hermetic.

The leak seam is the shipped one (``harness.corpus.materialize.agent_visible_leak``);
the hermetic surface is reconstructed from the builder's own stdlib helpers (prompt
+ workspace files — exactly what ``emit_out`` inlines into ``tasks.yaml``, minus the
binary-built ``graph.json`` a call graph that cannot carry holdout content).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from harness.corpus.materialize import agent_visible_leak

_REPO = Path(__file__).resolve().parents[1]
_CORPUS = _REPO / "corpora" / "groundwork-v0"
_BUILDER = _CORPUS / "build_tasks.py"

# Expected inventory + class ratios (README): 6 reach, 4 obligation, 4 null, 3 multi.
# gw-r5b is the de-baited variant of gw-r5 [design: mechanism-decomposition piece 3].
_EXPECTED_CLASS = {
    "gw-r1": "reach-trap", "gw-r2": "reach-trap", "gw-r3": "reach-trap",
    "gw-r4": "reach-trap", "gw-r5": "reach-trap", "gw-r5b": "reach-trap",
    "gw-o1": "obligation-trap", "gw-o2": "obligation-trap",
    "gw-o3": "obligation-trap", "gw-o4": "obligation-trap",
    "gw-n1": "null", "gw-n2": "null", "gw-n3": "null", "gw-n4": "null",
    "gw-m1": "multi-impl", "gw-m2": "multi-impl", "gw-m3": "multi-impl",
}
_EXPECTED_RATIOS = {"reach-trap": 6, "obligation-trap": 4, "null": 4, "multi-impl": 3}

_BINARIES = bool(os.environ.get("VERDI_FLOWMAP_BIN") and os.environ.get("VERDI_GROUNDWORK_BIN"))
_needs_binaries = pytest.mark.skipif(
    not _BINARIES,
    reason="set VERDI_FLOWMAP_BIN + VERDI_GROUNDWORK_BIN (built from verdi-go) to run "
    "the builder's binary-backed --check / --out",
)


def _load_builder():
    """Load the stdlib-only builder by file path — it imports NO harness code and
    (post env-override fix) resolves binaries lazily, so importing it needs no
    toolchain."""
    spec = importlib.util.spec_from_file_location("gwv0_build_tasks", _BUILDER)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


bt = _load_builder()


# --------------------------------------------------------------------------- #
# fast hermetic subset (no binaries)
# --------------------------------------------------------------------------- #
def test_inventory_17_tasks_expected_classes():
    got = {t["id"]: t["class"] for t in bt.discover_tasks()}
    assert got == _EXPECTED_CLASS
    assert Counter(got.values()) == _EXPECTED_RATIOS


def test_every_task_has_a_hidden_feature_test_absent_from_workspace():
    """The acceptance test lives only in ``solution/`` (+ the holdout), never in the
    agent's ``workspace/`` — the base must ship green so ONLY the invariant, not a
    missing test, distinguishes the tempting impl from the disciplined one [§5]."""
    for t in bt.discover_tasks():
        tests = bt.feature_tests(t)
        assert tests, f"{t['id']}: no hidden feature test (solution adds no *_test.go)"
        ws_files = bt.workspace_files(t)
        for rel, _ in tests:
            assert rel not in ws_files, f"{t['id']}: feature test {rel} leaked into workspace/"


def test_workspace_policy_and_graph_are_agent_visible():
    """9c parity: ``policy.json`` + ``graph.json`` ARE in the agent-visible
    workspace — intended (both arms have identical epistemic access; a gate the
    agent can read is the whole premise), NOT a leak [§6 interpretation note 1]."""
    for t in bt.discover_tasks():
        ws_files = bt.workspace_files(t)
        assert "policy.json" in ws_files, f"{t['id']}: policy.json not agent-visible"
        assert "graph.json" in ws_files, f"{t['id']}: graph.json not agent-visible"


def test_no_holdout_material_in_agent_visible_surface():
    """The per-task canary and the hidden feature-test body must never appear in the
    agent-visible surface (prompt + workspace files) [§5, AC-9]. Uses the shipped
    leak seam (``agent_visible_leak``) on the stdlib surface so it runs without
    binaries; the binary-gated test repeats it against the real ``tasks.yaml``."""
    for t in bt.discover_tasks():
        tid = t["id"]
        canary = bt.task_canary(tid)
        tests = bt.feature_tests(t)
        # what emit_out inlines into tasks.yaml for the agent: prompt + workspace
        # files. graph.json (binary-built) is omitted here — a flowmap call graph
        # excludes test files, so it cannot carry the canary or the feature test.
        surface = t["prompt"] + "".join(bt.workspace_files(t).values())
        needles = [canary] + [content for _, content in tests]
        leak = agent_visible_leak(surface, needles)
        assert leak is None, f"{tid}: holdout material reachable from agent surface: {leak[:80]!r}"
        # the canary is added ONLY at holdout materialization — assert it is not
        # already in the raw feature test, else the leak scan would be vacuous.
        for _, content in tests:
            assert canary not in content, f"{tid}: canary present in raw feature test?"


def test_task_meta_schema_and_substrate_agreement():
    """Each ``task.meta.json`` carries the fields admission reads, its committed
    ``workspace/graph.json`` parses and is tool-stamped (provenance), and its graph
    ``algo`` equals the declared substrate (``discover_tasks`` separately enforces
    policy↔meta substrate agreement, so a disagreement would already have raised)."""
    for t in bt.discover_tasks():
        meta = t["meta"]
        for key in ("id", "class", "binding_rule", "graph_substrate",
                    "exemplar_expected_verify_rc"):
            assert key in meta, f"{t['id']}: task.meta.json missing {key!r}"
        graph = json.loads((t["dir"] / "workspace" / "graph.json").read_text(encoding="utf-8"))
        assert graph.get("tool"), f"{t['id']}: committed graph.json has no tool stamp"
        assert graph.get("algo") == t["substrate"], (
            f"{t['id']}: committed graph.json algo {graph.get('algo')!r} != "
            f"substrate {t['substrate']!r}"
        )


def test_holdout_argv_is_portable_no_container_absolute_paths():
    """The composite holdout resolves the holdouts root as
    ``${VERDI_HOLDOUTS_DIR:-/holdouts}`` and invokes the wrapper by BARE NAME, so it
    runs both in the grade container and off-container (ADVISORY local-exec) [§3].
    Guards the two fixed regressions: the container-absolute wrapper path, and the
    ``/holdouts/<id>/functional`` cp path that mis-addressed the per-task mount."""
    for t in bt.discover_tasks():
        argv = bt.holdout_argv(t["id"], bt.feature_tests(t))
        assert argv[:2] == ["sh", "-c"]
        script = argv[2]
        assert "${VERDI_HOLDOUTS_DIR:-/holdouts}" in script
        assert "/usr/local/bin/verdi-groundwork-check" not in script
        assert "verdi-groundwork-check " in script
        assert f"/holdouts/{t['id']}/functional" not in script


# --------------------------------------------------------------------------- #
# binary-gated tail (SKIPPED unless VERDI_FLOWMAP_BIN is set — keeps make verify
# hermetic)
# --------------------------------------------------------------------------- #
@_needs_binaries
def test_builder_check_exits_zero():
    """The corpus's own (a)/(b)/(c) validation matrix is green under the pinned
    toolchain (``build_tasks.py --check`` → ``ALL CELLS GREEN``, exit 0)."""
    proc = subprocess.run(
        [sys.executable, str(_BUILDER), "--check"],
        cwd=str(_CORPUS), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL CELLS GREEN" in proc.stdout


@_needs_binaries
def test_emitted_tasks_yaml_has_no_canary_leak(tmp_path):
    """Full-fidelity leak scan: emit the real experiment dir and assert no task's
    canary appears anywhere in the agent-visible ``tasks.yaml`` [AC-9]."""
    out = tmp_path / "expt"
    proc = subprocess.run(
        [sys.executable, str(_BUILDER), "--out", str(out)],
        cwd=str(_CORPUS), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tasks_yaml = (out / "tasks.yaml").read_text(encoding="utf-8")
    canaries = [bt.task_canary(t["id"]) for t in bt.discover_tasks()]
    leak = agent_visible_leak(tasks_yaml, canaries)
    assert leak is None, f"canary leaked into tasks.yaml: {leak!r}"
