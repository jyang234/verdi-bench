"""Flagship authoring kit — hermetic properties [verdi-go integration plan §6 / §10 P4].

Pins the behavior of ``scripts/flagship/{author_pilot,author_flagship,costmodel}``
and the shared ``scripts/shakedown/_groundwork_lib`` selection helpers, WITHOUT
network, Docker, keys, or the verdi-go toolchain: a synthetic corpus-out fixture
(a ``tasks.yaml`` + ``holdouts/`` shaped exactly like ``build_tasks.py --out``, but
with trivial file contents) stands in for the binary-built corpus, and the schema
is the real ``harness.schema`` / SDK build path.

Covered:
* pilot authoring is DETERMINISTIC (two runs → byte-identical experiment.yaml /
  tasks.yaml / run.config.yaml for both pilot experiments);
* the authored pilot + flagship specs VALIDATE through the real schema;
* the D4 decision table picks 2×2 vs staged correctly on synthetic cost inputs;
* the opus slice + subset stratification cover all four corpus classes;
* the pilot design SCALES with the ceiling and REFUSES loudly below the minimum;
* MDE-driven repetitions come from the real ``plan/power.py`` seams, deterministically;
* judge-model validation fails loud (missing / wrong-vendor prefix / un-versioned alias).
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
import author_flagship as af  # noqa: E402
import author_pilot as ap  # noqa: E402
import costmodel  # noqa: E402

# Mirrors the real groundwork-v0 class ratios (5 reach / 4 obligation / 4 null /
# 3 multi-impl) so n_corpus and stratification behave as on the real corpus.
_CORPUS_TASKS = [
    ("gw-r1", "reach-trap"), ("gw-r2", "reach-trap"), ("gw-r3", "reach-trap"),
    ("gw-r4", "reach-trap"), ("gw-r5", "reach-trap"),
    ("gw-o1", "obligation-trap"), ("gw-o2", "obligation-trap"),
    ("gw-o3", "obligation-trap"), ("gw-o4", "obligation-trap"),
    ("gw-n1", "null"), ("gw-n2", "null"), ("gw-n3", "null"), ("gw-n4", "null"),
    ("gw-m1", "multi-impl"), ("gw-m2", "multi-impl"), ("gw-m3", "multi-impl"),
]
_ALL_CLASSES = {"reach-trap", "obligation-trap", "null", "multi-impl"}


@pytest.fixture
def corpus_out(tmp_path: Path) -> Path:
    """A synthetic ``build_tasks.py --out`` directory (tasks.yaml + holdouts/)."""
    root = tmp_path / "corpus-out"
    tasks = []
    for tid, cls in _CORPUS_TASKS:
        (root / "holdouts" / tid).mkdir(parents=True, exist_ok=True)
        (root / "holdouts" / tid / "holdout.json").write_text(
            json.dumps({"schema_version": 1, "kind": "command",
                        "id": f"{tid}-h", "argv": ["sh", "-c", "true"]}) + "\n",
            encoding="utf-8")
        tasks.append({
            "id": tid, "prompt": f"solve {tid}", "timeout_s": 900, "task_class": cls,
            "plugin_ids": ["groundwork"], "holdouts_dir": f"holdouts/{tid}",
            "holdout_canaries": [f"GWV0-{tid}-HOLDOUT-CANARY"],
            "files": {"main.go": "package main\n", "graph.json": "{}\n"},
        })
    (root / "tasks.yaml").write_text(
        json.dumps({"tasks": tasks}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# cost model
# --------------------------------------------------------------------------- #
def test_cost_model_estimates_pin_the_profile():
    # Pins the documented conservative per-trial estimate (a silent change to the
    # token profile / price table is caught here). opus is the expensive tier.
    assert costmodel.est_cost_per_trial("opus") == 1.5
    assert costmodel.est_cost_per_trial("haiku") == 0.3
    assert costmodel.est_cost_per_trial("opus") > costmodel.est_cost_per_trial("haiku")
    with pytest.raises(ValueError):
        costmodel.est_cost_per_trial("sonnet")


# --------------------------------------------------------------------------- #
# pilot: determinism, schema, class coverage, scaling, refusal
# --------------------------------------------------------------------------- #
def _pilot_bytes(pilot_dir: Path) -> dict[str, bytes]:
    out = {}
    for sub in (ap.CALIBRATION_DIR, ap.OPUS_SLICE_DIR):
        for fname in ("experiment.yaml", "tasks.yaml", "run.config.yaml"):
            out[f"{sub}/{fname}"] = (pilot_dir / sub / fname).read_bytes()
    return out


def test_pilot_authoring_is_byte_deterministic(corpus_out: Path, tmp_path: Path):
    a = ap.author_pilot(corpus_out, tmp_path / "a", ceiling=10, seed=1234, quiet=True)
    b = ap.author_pilot(corpus_out, tmp_path / "b", ceiling=10, seed=1234, quiet=True)
    assert a.haiku_ids == b.haiku_ids and a.opus_ids == b.opus_ids
    assert _pilot_bytes(tmp_path / "a") == _pilot_bytes(tmp_path / "b")


def test_pilot_specs_validate_through_the_real_schema(corpus_out: Path, tmp_path: Path):
    from harness.schema.experiment import ExperimentSpec
    from harness.schema.tasks import TaskSpec

    r = ap.author_pilot(corpus_out, tmp_path / "p", ceiling=10, seed=1234, quiet=True)
    for sub in (r.haiku_dir, r.opus_dir):
        spec = ExperimentSpec.from_yaml(sub / "experiment.yaml")  # raises on invalid
        assert len(spec.arms) == 2
        assert spec.judge.model == gw.PLACEHOLDER_JUDGE  # never-invoked placeholder
        import yaml
        for t in yaml.safe_load((sub / "tasks.yaml").read_text())["tasks"]:
            TaskSpec(**t)  # write-side validation, extra=forbid
        # every referenced task's holdouts tree was copied in beside the spec
        for t in yaml.safe_load((sub / "tasks.yaml").read_text())["tasks"]:
            assert (sub / t["holdouts_dir"] / "holdout.json").exists()


def test_pilot_run_config_declares_both_hosts_and_arm_keys(corpus_out: Path, tmp_path: Path):
    import yaml

    r = ap.author_pilot(corpus_out, tmp_path / "p", ceiling=10, seed=1234, quiet=True)
    cfg = yaml.safe_load((r.haiku_dir / "run.config.yaml").read_text())
    assert set(cfg["proxy"]["allowlist"]) == {"api.anthropic.com", "api.openai.com"}
    # every arm gets the anthropic key; the openai judge key is NOT here (host env).
    for names in cfg["provider_key_names_by_arm"].values():
        assert names == ["ANTHROPIC_API_KEY"]


def test_pilot_subset_and_opus_slice_cover_all_four_classes(corpus_out: Path, tmp_path: Path):
    task_dicts = gw.load_corpus_tasks(corpus_out)
    r = ap.author_pilot(corpus_out, tmp_path / "p", ceiling=10, seed=1234, quiet=True)
    haiku_classes = gw.classes_of(task_dicts, r.haiku_ids)
    opus_classes = gw.classes_of(task_dicts, r.opus_ids)
    # the stratified haiku subset covers ALL four classes on its own …
    assert haiku_classes == _ALL_CLASSES
    # … and the opus slice + subset together cover all four (the union).
    assert (haiku_classes | opus_classes) == _ALL_CLASSES
    # the opus slice is small (2..cap) and its smallest form samples a trap + the null.
    assert 2 <= len(r.opus_ids) <= costmodel.OPUS_SLICE_CAP
    assert "null" in opus_classes and (opus_classes - {"null"})  # >= one non-null (trap)


def test_pilot_design_scales_monotonically_with_ceiling(corpus_out: Path, tmp_path: Path):
    small = ap.author_pilot(corpus_out, tmp_path / "s", ceiling=10, seed=1234, quiet=True).design
    big = ap.author_pilot(corpus_out, tmp_path / "b", ceiling=50, seed=1234, quiet=True).design
    # a bigger ceiling buys a >= design on every axis, and never overruns its ceiling.
    assert big.haiku_subset >= small.haiku_subset
    assert big.reps >= small.reps
    assert big.opus_slice >= small.opus_slice
    assert (big.haiku_subset, big.reps, big.opus_slice) != (small.haiku_subset, small.reps, small.opus_slice)
    for d in (small, big):
        assert d.total_projected <= d.ceiling
    # $50 buys the fuller design the owner directive describes: 2 reps + larger opus slice.
    assert big.reps == 2 and big.opus_slice > small.opus_slice


def test_pilot_refuses_loudly_below_the_minimum(corpus_out: Path, tmp_path: Path):
    # opus dominates: the minimal design ($8.40 at the estimates) cannot fit $4.
    with pytest.raises(costmodel.CeilingTooLowError):
        ap.author_pilot(corpus_out, tmp_path / "x", ceiling=4, seed=1234, quiet=True)


# --------------------------------------------------------------------------- #
# D4 decision table
# --------------------------------------------------------------------------- #
def test_d4_picks_2x2_when_projection_fits():
    proj = costmodel.project_flagship(
        n_tasks=16, reps=1, cost_haiku_trial=0.30, cost_opus_trial=1.50,
        flagship_ceiling=100.0)
    # 2x2 solve = 16*1*(2*1.5 + 2*0.3) = 57.6 <= 100 → 2x2.
    assert proj.two_by_two.solve_cost == pytest.approx(57.6)
    assert costmodel.decide_d4(proj) == "2x2"


def test_d4_picks_staged_when_2x2_overruns():
    proj = costmodel.project_flagship(
        n_tasks=16, reps=3, cost_haiku_trial=0.30, cost_opus_trial=2.00,
        flagship_ceiling=40.0)
    # 2x2 solve = 16*3*(2*2 + 2*0.3) = 220.8 > 40 → staged; staged = 16*3*0.6 = 28.8 <= 40.
    assert proj.two_by_two.total > 40.0 >= proj.staged.total
    assert costmodel.decide_d4(proj) == "staged"


def test_d4_boundary_is_inclusive():
    # exactly at the ceiling counts as fitting (<=), so 2x2 is chosen.
    proj = costmodel.project_flagship(
        n_tasks=1, reps=1, cost_haiku_trial=0.0 + 1.0, cost_opus_trial=1.0,
        flagship_ceiling=4.0)  # 2x2 solve = 1*1*(2*1 + 2*1) = 4.0 == ceiling
    assert proj.two_by_two.total == 4.0
    assert costmodel.decide_d4(proj) == "2x2"


# --------------------------------------------------------------------------- #
# flagship: MDE reps, authoring, schema, determinism, judge validation
# --------------------------------------------------------------------------- #
def test_mde_reps_are_deterministic_and_from_power_seams():
    from harness.plan.power import CalibrationVariance

    var = CalibrationVariance(p=0.5, rho=0.3, n_tasks=16)
    a = af.recommend_reps(var, n_tasks=16, seed=1234, target_mde=0.3, max_reps=4,
                          n_sim=20, n_boot=60)
    b = af.recommend_reps(var, n_tasks=16, seed=1234, target_mde=0.3, max_reps=4,
                          n_sim=20, n_boot=60)
    assert a == b                       # deterministic (seeded sim)
    chosen, achieved, curve = a
    assert len(curve) == 4              # swept reps 1..4
    if chosen is not None:              # if a rep reaches the target, its MDE is <= target
        assert achieved is not None and achieved <= 0.3


def _flagship(corpus_out, out, *, ceiling, co, ch=0.30, **kw):
    kw.setdefault("cal_p", 0.5)  # overridable (a manifest or explicit None)
    return af.author_flagship(
        corpus_out, out, judge_model="openai/gpt-5.1-2025-11-01",
        flagship_ceiling=ceiling, cost_per_trial_haiku=ch, cost_per_trial_opus=co,
        target_mde=0.3, max_reps=3, n_sim=12, n_boot=40, quiet=True, **kw)


def test_flagship_authors_2x2_and_validates(corpus_out: Path, tmp_path: Path):
    from harness.schema.experiment import ExperimentSpec

    r = _flagship(corpus_out, tmp_path / "f", ceiling=1000.0, co=0.50)
    assert r.chosen == "2x2"
    spec = ExperimentSpec.from_yaml(tmp_path / "f" / "experiment.yaml")
    assert [a.name for a in spec.arms] == ["opus-bare", "opus-grounded", "haiku-bare", "haiku-grounded"]
    assert spec.multi_arm_correction == "holm"
    assert spec.judge.model == "openai/gpt-5.1-2025-11-01"
    assert spec.hypothesized_effect == 0.3                 # power target emitted for the plan gate
    assert spec.decision_rule == "delta_holdout_pass_rate >= 0.3"
    assert spec.repetitions == r.reps
    # payload asymmetry: grounded arms carry the tool, bare arms are empty.
    by_name = {a.name: a for a in spec.arms}
    assert by_name["opus-grounded"].payload == gw.GROUNDED_PAYLOAD
    assert by_name["opus-bare"].payload == {}
    # all corpus holdouts were copied in beside the spec.
    assert sorted(p.name for p in (tmp_path / "f" / "holdouts").iterdir()) == \
        sorted(tid for tid, _ in _CORPUS_TASKS)


def test_flagship_authors_staged_when_opus_is_dear(corpus_out: Path, tmp_path: Path):
    from harness.schema.experiment import ExperimentSpec

    r = _flagship(corpus_out, tmp_path / "f", ceiling=20.0, co=5.00)
    assert r.chosen == "staged"
    spec = ExperimentSpec.from_yaml(tmp_path / "f" / "experiment.yaml")
    assert [a.name for a in spec.arms] == ["haiku-bare", "haiku-grounded"]
    assert spec.multi_arm_correction == "none"  # 2-arm, inert


def test_flagship_authoring_is_byte_deterministic(corpus_out: Path, tmp_path: Path):
    _flagship(corpus_out, tmp_path / "a", ceiling=1000.0, co=0.50)
    _flagship(corpus_out, tmp_path / "b", ceiling=1000.0, co=0.50)
    for f in ("experiment.yaml", "tasks.yaml", "run.config.yaml"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_flagship_uses_calibration_variance_when_manifest_present(corpus_out: Path, tmp_path: Path):
    # A manifest carrying a `bench corpus calibrate` run makes the design NOT
    # assumption-based; without it, AssumedVariance is flagged.
    from harness.corpus.registry import Calibration, CorpusManifest

    manifest = CorpusManifest(
        corpus_id="groundwork-v0", semver="0.0.0", kind="public",
        calibration=Calibration(status="full-run-validated",
                                 runs=[{"p": 0.6, "rho": 0.3, "n_tasks": 16, "kind": "full"}]))
    mpath = tmp_path / "manifest.json"
    manifest.save(mpath)
    r = _flagship(corpus_out, tmp_path / "f", ceiling=1000.0, co=0.50, pilot_manifest=mpath)
    assert r.variance_kind == "CalibrationVariance" and r.assumption_based is False

    r2 = _flagship(corpus_out, tmp_path / "g", ceiling=1000.0, co=0.50, cal_p=None)
    assert r2.variance_kind == "AssumedVariance" and r2.assumption_based is True


def test_judge_model_prefix_validation_fails_loud(corpus_out: Path, tmp_path: Path):
    # missing → refusal
    with pytest.raises(af.JudgeModelError):
        af.validate_judge_model(None)
    # wrong vendor → refusal (D5 fixes the vendor to OpenAI)
    with pytest.raises(af.JudgeModelError):
        af.validate_judge_model("anthropic/claude-opus-4-8-20260101")
    # un-versioned OpenAI alias passes the prefix guard but the SCHEMA rejects it at build.
    from harness.schema.errors import AliasJudgeIdError

    with pytest.raises(AliasJudgeIdError):
        af.author_flagship(
            corpus_out, tmp_path / "x", judge_model="openai/gpt-5",
            flagship_ceiling=1000.0, cost_per_trial_haiku=0.3, cost_per_trial_opus=0.5,
            cal_p=0.5, target_mde=0.3, max_reps=2, n_sim=8, n_boot=20, quiet=True)
