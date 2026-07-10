"""Consistency-program authoring kit — hermetic properties [verdi-go plan §6 / §10 P4].

Pins ``scripts/flagship/author_consistency`` (the recon authoring) and
``scripts/flagship/attest_models`` (the per-trial model verifier) WITHOUT network,
Docker, keys, or the verdi-go toolchain. A synthetic ``build_tasks.py --out``
corpus fixture (the same shape ``test_flagship_author`` uses) stands in for the
binary-built corpus; the schema/SDK build path is the real one.

Covered:
* author_consistency REFUSES over-ceiling (incl. at the DEFAULT reps/ceiling) and a
  short/mismatched corpus, with NO partial write; it writes the expected
  arms/reps/model/cost_ceiling + all 17 holdouts, byte-deterministically;
* attest_models classifies OK / MISMATCH / NO-NATIVE-LOG correctly over a synthetic
  experiment (hand-built ledger + artifacts), never skips a missing log, and its
  exit code is 0 iff every trial is OK. The [1m]-suffix and empty-modelUsage rules
  are pinned on the pure ``classify`` core.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts" / "flagship"))
sys.path.insert(0, str(_REPO / "scripts" / "shakedown"))

import _groundwork_lib as gw  # noqa: E402
import attest_models as am  # noqa: E402
import author_consistency as ac  # noqa: E402
import costmodel  # noqa: E402

# The real groundwork-v0 corpus set (6 reach / 4 obligation / 4 null / 3 multi-impl).
_CORPUS_TASKS = [
    ("gw-r1", "reach-trap"), ("gw-r2", "reach-trap"), ("gw-r3", "reach-trap"),
    ("gw-r4", "reach-trap"), ("gw-r5", "reach-trap"), ("gw-r5b", "reach-trap"),
    ("gw-o1", "obligation-trap"), ("gw-o2", "obligation-trap"),
    ("gw-o3", "obligation-trap"), ("gw-o4", "obligation-trap"),
    ("gw-n1", "null"), ("gw-n2", "null"), ("gw-n3", "null"), ("gw-n4", "null"),
    ("gw-m1", "multi-impl"), ("gw-m2", "multi-impl"), ("gw-m3", "multi-impl"),
]


def _materialize_corpus(root: Path, tasks: list[tuple[str, str]]) -> Path:
    """A synthetic ``build_tasks.py --out`` dir (tasks.yaml + holdouts/)."""
    entries = []
    for tid, cls in tasks:
        (root / "holdouts" / tid).mkdir(parents=True, exist_ok=True)
        (root / "holdouts" / tid / "holdout.json").write_text(
            json.dumps({"schema_version": 1, "kind": "command",
                        "id": f"{tid}-h", "argv": ["sh", "-c", "true"]}) + "\n",
            encoding="utf-8")
        entries.append({
            "id": tid, "prompt": f"solve {tid}", "timeout_s": 900, "task_class": cls,
            "plugin_ids": ["groundwork"], "holdouts_dir": f"holdouts/{tid}",
            "holdout_canaries": [f"GWV0-{tid}-HOLDOUT-CANARY"],
            "files": {"main.go": "package main\n", "graph.json": "{}\n"},
        })
    (root / "tasks.yaml").write_text(
        json.dumps({"tasks": entries}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def corpus_out(tmp_path: Path) -> Path:
    return _materialize_corpus(tmp_path / "corpus-out", _CORPUS_TASKS)


# --------------------------------------------------------------------------- #
# author_consistency: projection, refusal, spec, determinism
# --------------------------------------------------------------------------- #
def test_recon_projection_matches_corpus_size():
    n = len(ac.EXPECTED_TASK_IDS)  # the whole corpus (17), derived — no silent subset
    ch = costmodel.est_cost_per_trial("haiku")
    d = ac.ReconDesign(n_tasks=n, reps=5, tier="haiku", cost_per_trial=ch, ceiling=35.0)
    assert d.trials == n * 5 * 2
    assert d.projected == round(n * 5 * 2 * ch, 4)
    # the DEFAULT invocation (reps=5, ceiling=35) refuses on the conservative estimate.
    assert not d.fits
    assert ac.ReconDesign(n_tasks=n, reps=3, tier="haiku", cost_per_trial=ch, ceiling=35.0).fits
    # an unpriced tier has an honestly-UNKNOWN projection (None), which the ceiling
    # fence cannot judge (fits — the explicit-ceiling requirement is the guard).
    u = ac.ReconDesign(n_tasks=n, reps=5, tier="sonnet", cost_per_trial=None, ceiling=50.0)
    assert u.projected is None and u.fits


def test_author_consistency_refuses_over_ceiling_at_defaults(corpus_out: Path, tmp_path: Path):
    out = tmp_path / "recon"
    # defaults: reps=5 x 17 x 2 x $0.30 = $51.00 > default $35 ceiling → refuse.
    with pytest.raises(costmodel.CeilingTooLowError):
        ac.author_consistency(corpus_out, out, trial_image="sha256:deadbeef",
                              workflow="availability", quiet=True)
    assert not out.exists()  # no partial write


def test_author_consistency_refuses_over_ceiling_explicit(corpus_out: Path, tmp_path: Path):
    out = tmp_path / "recon"
    with pytest.raises(costmodel.CeilingTooLowError):
        ac.author_consistency(corpus_out, out, trial_image="sha256:x", reps=1,
                              ceiling=5.0, workflow="availability", quiet=True)
    assert not out.exists()


def test_author_consistency_refuses_short_corpus(tmp_path: Path):
    short = _materialize_corpus(tmp_path / "short", _CORPUS_TASKS[:-1])  # 16 tasks
    out = tmp_path / "recon"
    with pytest.raises(ac.ConsistencyRefusal):
        ac.author_consistency(short, out, trial_image="sha256:x", reps=1, ceiling=100.0,
                              workflow="availability", quiet=True)
    assert not out.exists()  # a short corpus is refused, never silently subset


def test_author_consistency_writes_expected_spec(corpus_out: Path, tmp_path: Path):
    from harness.schema.experiment import ExperimentSpec

    out = tmp_path / "recon"
    r = ac.author_consistency(corpus_out, out, trial_image="sha256:deadbeef",
                              reps=3, ceiling=35.0, workflow="ground_verify", quiet=True)
    assert (r.design.n_tasks, r.design.reps, r.design.trials) == (17, 3, 102)

    spec = ExperimentSpec.from_yaml(out / "experiment.yaml")
    by_name = {a.name: a for a in spec.arms}
    assert set(by_name) == {"haiku-bare", "haiku-grounded"}
    assert all(a.model == gw.MODEL_HAIKU for a in spec.arms)  # tool A/B at a fixed tier
    assert all(a.platform == "claude_code" for a in spec.arms)
    # payload asymmetry: grounded carries the tool + the instructed rung's workflow
    # key (built locally by the kit, exact); bare is empty.
    assert by_name["haiku-grounded"].payload == {"tools": ["groundwork"],
                                                 "workflow": "ground_verify"}
    assert by_name["haiku-bare"].payload == {}
    assert spec.repetitions == 3
    assert spec.cost_ceiling.amount == 35.0
    assert spec.judge.model == gw.PLACEHOLDER_JUDGE  # never-invoked, grade-only

    import yaml
    task_ids = [t["id"] for t in yaml.safe_load((out / "tasks.yaml").read_text())["tasks"]]
    assert sorted(task_ids) == sorted(ac.EXPECTED_TASK_IDS)  # ALL 17, no subset
    assert sorted(p.name for p in (out / "holdouts").iterdir()) == sorted(ac.EXPECTED_TASK_IDS)
    # managed-proxy run.config: both hosts, anthropic key per arm (mirrors the pilot).
    cfg = yaml.safe_load((out / "run.config.yaml").read_text())
    assert set(cfg["proxy"]["allowlist"]) == {"api.anthropic.com", "api.openai.com"}
    assert set(cfg["provider_key_names_by_arm"]) == {"haiku-bare", "haiku-grounded"}
    for names in cfg["provider_key_names_by_arm"].values():
        assert names == ["ANTHROPIC_API_KEY"]


def test_author_consistency_is_byte_deterministic(corpus_out: Path, tmp_path: Path):
    # per-rung determinism: two identical invocations byte-equal, for BOTH rungs.
    for rung in ("availability", "ground_verify"):
        ac.author_consistency(corpus_out, tmp_path / f"{rung}-a", trial_image="sha256:x",
                              reps=2, ceiling=35, workflow=rung, quiet=True)
        ac.author_consistency(corpus_out, tmp_path / f"{rung}-b", trial_image="sha256:x",
                              reps=2, ceiling=35, workflow=rung, quiet=True)
        for f in ("experiment.yaml", "tasks.yaml", "run.config.yaml"):
            assert (tmp_path / f"{rung}-a" / f).read_bytes() == \
                (tmp_path / f"{rung}-b" / f).read_bytes()
    # the rungs differ from each other by exactly the grounded payload delta.
    assert (tmp_path / "availability-a" / "experiment.yaml").read_bytes() != \
        (tmp_path / "ground_verify-a" / "experiment.yaml").read_bytes()


# --------------------------------------------------------------------------- #
# author_consistency: the explicit workflow rung (--workflow)
# --------------------------------------------------------------------------- #
def test_workflow_is_required_no_default(corpus_out: Path, tmp_path: Path):
    """The rung must be explicit in every authoring command: omitting it refuses
    (required keyword — TypeError) with no partial write, and an unknown rung is
    a loud ConsistencyRefusal, also with no write."""
    out = tmp_path / "recon"
    with pytest.raises(TypeError):
        ac.author_consistency(corpus_out, out, trial_image="sha256:x", reps=1,
                              ceiling=35.0, quiet=True)
    assert not out.exists()
    with pytest.raises(ac.ConsistencyRefusal):
        ac.author_consistency(corpus_out, out, trial_image="sha256:x", reps=1,
                              ceiling=35.0, workflow="vibes", quiet=True)
    assert not out.exists()


def test_rung_payloads_exact(corpus_out: Path, tmp_path: Path):
    """availability → grounded payload WITHOUT a workflow key; ground_verify →
    exactly the §6 instructed payload. Bare stays empty in both."""
    from harness.schema.experiment import ExperimentSpec

    ac.author_consistency(corpus_out, tmp_path / "avail", trial_image="sha256:x",
                          reps=1, ceiling=35.0, workflow="availability", quiet=True)
    ac.author_consistency(corpus_out, tmp_path / "instr", trial_image="sha256:x",
                          reps=1, ceiling=35.0, workflow="ground_verify", quiet=True)
    avail = {a.name: a for a in ExperimentSpec.from_yaml(
        tmp_path / "avail" / "experiment.yaml").arms}
    instr = {a.name: a for a in ExperimentSpec.from_yaml(
        tmp_path / "instr" / "experiment.yaml").arms}
    assert avail["haiku-grounded"].payload == {"tools": ["groundwork"]}
    assert "workflow" not in avail["haiku-grounded"].payload
    assert instr["haiku-grounded"].payload == {"tools": ["groundwork"],
                                               "workflow": "ground_verify"}
    assert avail["haiku-bare"].payload == {} and instr["haiku-bare"].payload == {}


def test_enforced_rung_registered_and_payload_exact():
    """The enforced rung (rung 3) is a registered --workflow choice and maps to the
    grounded payload the trial agent gates the Stop hook on."""
    assert "ground_verify_enforced" in ac.GROUNDED_PAYLOADS_BY_WORKFLOW
    assert ac.grounded_payload_for("ground_verify_enforced") == {
        "tools": ["groundwork"], "workflow": "ground_verify_enforced"}


def test_workflow_enforced_writes_enforced_payload(corpus_out: Path, tmp_path: Path):
    """--workflow ground_verify_enforced authors the enforced grounded payload; bare
    stays empty (arm-name tier logic unchanged from the other rungs)."""
    from harness.schema.experiment import ExperimentSpec

    out = tmp_path / "enforced"
    ac.author_consistency(corpus_out, out, trial_image="sha256:x", reps=1, ceiling=35.0,
                          workflow="ground_verify_enforced", quiet=True)
    by_name = {a.name: a for a in ExperimentSpec.from_yaml(out / "experiment.yaml").arms}
    assert set(by_name) == {"haiku-bare", "haiku-grounded"}
    assert by_name["haiku-grounded"].payload == {"tools": ["groundwork"],
                                                 "workflow": "ground_verify_enforced"}
    assert by_name["haiku-bare"].payload == {}


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


# --------------------------------------------------------------------------- #
# author_consistency: cross-model baseline (--model)
# --------------------------------------------------------------------------- #
def test_tier_derivation_and_refusal():
    assert ac.derive_tier(gw.MODEL_HAIKU) == "haiku"
    assert ac.derive_tier(gw.MODEL_OPUS) == "opus"
    assert ac.derive_tier("anthropic/claude-sonnet-4-6-20260301") == "sonnet"
    # underivable ids are refused loudly — arm names/pricing are never guessed. A
    # provider-less id is refused too (the schema would reject it mid-write).
    for bad in ("openai/gpt-5.1-2025-11-01", "claude-haiku-4-5-20251001",
                "anthropic/claudette-4-5", "anthropic/claude--4"):
        with pytest.raises(ac.ConsistencyRefusal):
            ac.derive_tier(bad)


def test_default_model_is_byte_identical_to_explicit_haiku(corpus_out: Path, tmp_path: Path):
    """The --model parameterization is inert at the default: omitting it and
    passing MODEL_HAIKU explicitly author byte-identical experiments."""
    ac.author_consistency(corpus_out, tmp_path / "a", trial_image="sha256:x",
                          reps=2, ceiling=35, workflow="availability", quiet=True)
    ac.author_consistency(corpus_out, tmp_path / "b", trial_image="sha256:x",
                          reps=2, ceiling=35, workflow="availability", quiet=True,
                          model=gw.MODEL_HAIKU)
    for f in ("experiment.yaml", "tasks.yaml", "run.config.yaml", "rubric.md"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_non_haiku_model_writes_that_model_into_both_arms(corpus_out: Path, tmp_path: Path):
    """The cross-model baseline: the IDENTICAL experiment re-authored at another
    tier — same corpus/seed/reps/payloads, only the arm model + tier names change."""
    from harness.schema.experiment import ExperimentSpec

    r = ac.author_consistency(corpus_out, tmp_path / "opus", trial_image="sha256:x",
                              model=gw.MODEL_OPUS, reps=1, ceiling=100.0,
                              workflow="ground_verify", quiet=True)
    ac.author_consistency(corpus_out, tmp_path / "haiku", trial_image="sha256:x",
                          reps=1, ceiling=100.0, workflow="ground_verify", quiet=True)
    opus = ExperimentSpec.from_yaml(tmp_path / "opus" / "experiment.yaml")
    haiku = ExperimentSpec.from_yaml(tmp_path / "haiku" / "experiment.yaml")
    by_name = {a.name: a for a in opus.arms}
    assert set(by_name) == {"opus-bare", "opus-grounded"}
    assert all(a.model == gw.MODEL_OPUS for a in opus.arms)
    assert by_name["opus-grounded"].payload == {"tools": ["groundwork"],
                                                "workflow": "ground_verify"}
    assert by_name["opus-bare"].payload == {}
    # a priced tier uses costmodel's matching constant — never guesswork.
    assert r.design.cost_per_trial == costmodel.est_cost_per_trial("opus")
    assert r.design.projected == 51.0  # 17 x 1 x 2 x $1.50
    # NOTHING else varies across models: same seed, reps, task set, judge.
    assert opus.seed == haiku.seed
    assert opus.repetitions == haiku.repetitions
    assert opus.judge.model == haiku.judge.model
    import yaml
    ids = lambda p: [t["id"] for t in yaml.safe_load((p / "tasks.yaml").read_text())["tasks"]]  # noqa: E731
    assert ids(tmp_path / "opus") == ids(tmp_path / "haiku")
    cfg = yaml.safe_load((tmp_path / "opus" / "run.config.yaml").read_text())
    assert set(cfg["provider_key_names_by_arm"]) == {"opus-bare", "opus-grounded"}


def test_unknown_cost_model_requires_explicit_ceiling(corpus_out: Path, tmp_path: Path):
    sonnet = "anthropic/claude-sonnet-4-6-20260301"
    out = tmp_path / "recon-sonnet"
    # defaulted ceiling → refusal (pricing by guesswork), no partial write.
    with pytest.raises(ac.ConsistencyRefusal):
        ac.author_consistency(corpus_out, out, trial_image="sha256:x",
                              model=sonnet, reps=1, workflow="availability", quiet=True)
    assert not out.exists()
    # an explicit ceiling authors it, with the projection honestly UNKNOWN (None).
    from harness.schema.experiment import ExperimentSpec

    r = ac.author_consistency(corpus_out, out, trial_image="sha256:x",
                              model=sonnet, reps=1, ceiling=50.0,
                              workflow="availability", quiet=True)
    assert r.design.cost_per_trial is None and r.design.projected is None
    spec = ExperimentSpec.from_yaml(out / "experiment.yaml")
    assert {a.name for a in spec.arms} == {"sonnet-bare", "sonnet-grounded"}
    assert all(a.model == sonnet for a in spec.arms)
    assert spec.cost_ceiling.amount == 50.0


# --------------------------------------------------------------------------- #
# author_consistency: the explicit task subset (--tasks)
# --------------------------------------------------------------------------- #
def test_tasks_subset_authors_only_the_named_ids(corpus_out: Path, tmp_path: Path):
    """--tasks authors EXACTLY the named subset (sorted, deduped): tasks.yaml, the
    holdouts copied, and the projection all reflect only the selected ids."""
    import yaml
    from harness.schema.experiment import ExperimentSpec

    out = tmp_path / "subset"
    r = ac.author_consistency(corpus_out, out, trial_image="sha256:x", reps=2,
                              ceiling=35.0, workflow="availability",
                              tasks=["gw-o2", "gw-r1", "gw-n3"], quiet=True)
    assert r.ids == ["gw-n3", "gw-o2", "gw-r1"]  # sorted subset
    assert (r.design.n_tasks, r.design.trials) == (3, 3 * 2 * 2)

    task_ids = [t["id"] for t in yaml.safe_load((out / "tasks.yaml").read_text())["tasks"]]
    assert sorted(task_ids) == ["gw-n3", "gw-o2", "gw-r1"]  # only the subset authored
    assert sorted(p.name for p in (out / "holdouts").iterdir()) == [
        "gw-n3", "gw-o2", "gw-r1"]  # only the selected holdouts copied
    spec = ExperimentSpec.from_yaml(out / "experiment.yaml")
    assert {a.name for a in spec.arms} == {"haiku-bare", "haiku-grounded"}  # arms unchanged


def test_tasks_unknown_id_refuses_no_partial_write(corpus_out: Path, tmp_path: Path):
    """An id absent from the corpus is refused loudly, with NO partial write — an
    explicit subset must name only real tasks (never silently drop the typo)."""
    out = tmp_path / "subset"
    with pytest.raises(ac.ConsistencyRefusal):
        ac.author_consistency(corpus_out, out, trial_image="sha256:x", reps=1,
                              ceiling=35.0, workflow="availability",
                              tasks=["gw-r1", "gw-nope"], quiet=True)
    assert not out.exists()


def test_tasks_empty_selection_refuses(corpus_out: Path, tmp_path: Path):
    """An explicit but empty --tasks selection is refused (omit it to author all 17)."""
    out = tmp_path / "subset"
    with pytest.raises(ac.ConsistencyRefusal):
        ac.author_consistency(corpus_out, out, trial_image="sha256:x", reps=1,
                              ceiling=35.0, workflow="availability", tasks=[], quiet=True)
    assert not out.exists()


def test_tasks_omitted_authors_all_like_explicit_full_set(corpus_out: Path, tmp_path: Path):
    """Omitting --tasks authors the whole corpus (17), byte-identical to naming the
    full set explicitly — the default path is an unchanged whole-corpus author."""
    ac.author_consistency(corpus_out, tmp_path / "default", trial_image="sha256:x",
                          reps=2, ceiling=35, workflow="availability", quiet=True)
    ac.author_consistency(corpus_out, tmp_path / "full", trial_image="sha256:x",
                          reps=2, ceiling=35, workflow="availability", quiet=True,
                          tasks=sorted(ac.EXPECTED_TASK_IDS))
    for f in ("experiment.yaml", "tasks.yaml", "run.config.yaml"):
        assert (tmp_path / "default" / f).read_bytes() == (tmp_path / "full" / f).read_bytes()


# --------------------------------------------------------------------------- #
# attest_models: the pure classification rules
# --------------------------------------------------------------------------- #
def test_classify_pure_rules_and_prefix_strip():
    haiku = "claude-haiku-4-5-20251001"
    assert am.bare_model_id("anthropic/" + haiku) == haiku  # provider prefix stripped
    # OK: every modelUsage key equals the declared bare id.
    assert am.classify(haiku, {"modelUsage": {haiku: {"costUSD": 0.01}}})[0] == am.OK
    # MISMATCH: a different model (the defect — CLI ran its default).
    st, obs = am.classify(haiku, {"modelUsage": {"claude-opus-4-8[1m]": {}}})
    assert st == am.MISMATCH and "claude-opus-4-8[1m]" in obs
    # a [1m]-suffixed key mismatches an UNSUFFIXED declared id (uncontrolled variant)…
    assert am.classify("claude-opus-4-8", {"modelUsage": {"claude-opus-4-8[1m]": {}}})[0] == am.MISMATCH
    # …unless the declared id itself carries the suffix (exact equality).
    assert am.classify("claude-opus-4-8[1m]", {"modelUsage": {"claude-opus-4-8[1m]": {}}})[0] == am.OK
    # every key must match — one stray model is a MISMATCH.
    assert am.classify(haiku, {"modelUsage": {haiku: {}, "claude-opus-4-8[1m]": {}}})[0] == am.MISMATCH
    # empty modelUsage attests nothing → MISMATCH, never a vacuous OK.
    assert am.classify(haiku, {"modelUsage": {}})[0] == am.MISMATCH
    # a generic (non-native) verdi log has no modelUsage → NO-NATIVE-LOG.
    assert am.classify(haiku, {"verdi_log_version": 1, "trajectory": []})[0] == am.NO_NATIVE_LOG
    assert am.classify(haiku, "not even a dict")[0] == am.NO_NATIVE_LOG


# --------------------------------------------------------------------------- #
# attest_models: end to end over a synthetic experiment (no docker)
# --------------------------------------------------------------------------- #
_HAIKU_BARE = "claude-haiku-4-5-20251001"


def _synthetic_experiment(tmp_path: Path, trials: list[dict]) -> Path:
    """Author a real experiment.yaml (2 haiku arms) via the SDK, then hand-build a
    ledger + per-trial artifacts. ``trials`` items: {trial_id, arm, task_id, log}
    where ``log`` is the agent_log.json dict, or None to leave the dir empty, or
    "MISSING" to leave the artifacts dir absent entirely."""
    from harness.sdk import Experiment, Task

    expdir = tmp_path / "exp"
    exp = (Experiment("attest-fixture", seed=7, cost_ceiling_usd=5.0)
           .arm("haiku-grounded", model=gw.MODEL_HAIKU, platform="claude_code",
                payload=dict(gw.GROUNDED_PAYLOAD))
           .arm("haiku-bare", model=gw.MODEL_HAIKU, platform="claude_code", payload={})
           .judge(gw.PLACEHOLDER_JUDGE)
           .corpus("groundwork-v0", "0.0.0")
           .task(Task(id="gw-r1", prompt="x"))
           .repetitions(1))
    exp.write(expdir)  # writes experiment.yaml (valid); NO ledger

    events = []
    for t in trials:
        adir = tmp_path / "ws" / t["trial_id"] / "artifacts"
        if t["log"] != "MISSING":
            adir.mkdir(parents=True, exist_ok=True)
            if t["log"] is not None:
                (adir / am.AGENT_LOG_FILENAME).write_text(
                    json.dumps(t["log"]), encoding="utf-8")
        events.append({"event": "trial", "trial_record": {
            "trial_id": t["trial_id"], "arm": t["arm"], "task_id": t["task_id"],
            "artifacts_path": str(adir), "outcome": "completed"}})
    (expdir / "ledger.ndjson").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return expdir


def test_attest_classifies_ok_mismatch_and_no_native_log(tmp_path: Path):
    trials = [
        # native log naming the arm's declared model → OK
        {"trial_id": "t1", "arm": "haiku-bare", "task_id": "gw-r1",
         "log": {"type": "result", "modelUsage": {_HAIKU_BARE: {"costUSD": 0.02}}}},
        # native log naming a DIFFERENT model (the defect) → MISMATCH
        {"trial_id": "t2", "arm": "haiku-grounded", "task_id": "gw-r1",
         "log": {"type": "result", "modelUsage": {"claude-opus-4-8[1m]": {"costUSD": 0.9}}}},
        # a generic (non-native) verdi log → NO-NATIVE-LOG
        {"trial_id": "t3", "arm": "haiku-bare", "task_id": "gw-r2",
         "log": {"verdi_log_version": 1, "trajectory": []}},
        # a MISSING artifacts dir → loud NO-NATIVE-LOG, never skipped
        {"trial_id": "t4", "arm": "haiku-grounded", "task_id": "gw-r2", "log": "MISSING"},
    ]
    expdir = _synthetic_experiment(tmp_path, trials)

    rows = am.attest_experiment(expdir)
    assert len(rows) == 4  # the missing-log trial is NOT skipped
    by_id = {r.trial_id: r for r in rows}
    assert by_id["t1"].status == am.OK and by_id["t1"].declared == _HAIKU_BARE
    assert by_id["t2"].status == am.MISMATCH and "claude-opus-4-8[1m]" in by_id["t2"].observed
    assert by_id["t3"].status == am.NO_NATIVE_LOG
    assert by_id["t4"].status == am.NO_NATIVE_LOG
    # exit 1: not every trial is OK.
    assert am.main([str(expdir)]) == 1


def test_attest_all_ok_exits_zero(tmp_path: Path):
    trials = [
        {"trial_id": "t1", "arm": "haiku-bare", "task_id": "gw-r1",
         "log": {"modelUsage": {_HAIKU_BARE: {"costUSD": 0.02}}}},
        {"trial_id": "t2", "arm": "haiku-grounded", "task_id": "gw-r1",
         "log": {"modelUsage": {_HAIKU_BARE: {"costUSD": 0.03}}}},
    ]
    expdir = _synthetic_experiment(tmp_path, trials)
    rows = am.attest_experiment(expdir)
    assert [r.status for r in rows] == [am.OK, am.OK]
    assert am.main([str(expdir)]) == 0


def test_attest_unknown_arm_is_loud(tmp_path: Path):
    # a trial naming an arm absent from experiment.yaml is a ledger/spec integrity
    # failure — refused loudly, never silently attested.
    trials = [{"trial_id": "t1", "arm": "opus-bare", "task_id": "gw-r1",
               "log": {"modelUsage": {_HAIKU_BARE: {}}}}]
    expdir = _synthetic_experiment(tmp_path, trials)
    with pytest.raises(ValueError):
        am.attest_experiment(expdir)
    assert am.main([str(expdir)]) == 2  # structural error → exit 2
