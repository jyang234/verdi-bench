"""EVAL-20 — pre-registered multi-model arms.

The declared model set (primary + aux_models) feeds the four guarantees that
previously keyed off the single arm.model: blinding canaries [AC-2], judge
vendor overlap [AC-3], contamination dating [AC-4], and cross-vendor token
comparability [AC-5] — plus the declared-hosts egress attestation [AC-6,
D003]. Schema shape and named refusals are AC-1.
"""

from __future__ import annotations

import pytest

from harness.schema.errors import AuxModelError, ModelHostsError
from harness.schema.experiment import Arm, ExperimentSpec
from tests.fixtures.builders import (
    fixed_ctx,
    locked_experiment,
    valid_experiment_dict,
)

AUX_ARMS = [
    {
        "name": "control",
        "platform": "claude_code",
        "model": "anthropic/claude-3-5-sonnet-20241022",
        "training_cutoff": "2024-04-01T00:00:00Z",
        "payload": {},
    },
    {
        "name": "treatment",
        "platform": "generic",
        "model": "meta/llama-3-70b-instruct-20240620",
        "training_cutoff": "2024-06-01T00:00:00Z",
        "aux_models": [
            {
                "model": "qwen/qwen2-coder-32b-20240901",
                "training_cutoff": "2024-09-01T00:00:00Z",
            }
        ],
        "payload": {},
    },
]


def _spec(**overrides) -> ExperimentSpec:
    return ExperimentSpec.from_dict(valid_experiment_dict(**overrides))


# --- AC-1: schema -----------------------------------------------------------
def test_ac1_aux_models_schema():
    spec = _spec(arms=AUX_ARMS)
    treatment = spec.arms[1]
    assert treatment.declared_models() == [
        "meta/llama-3-70b-instruct-20240620",
        "qwen/qwen2-coder-32b-20240901",
    ]
    assert treatment.aux_models[0].training_cutoff == "2024-09-01T00:00:00Z"
    # pre-EVAL-20 arms (no aux_models key) validate unchanged
    assert _spec().arms[0].aux_models == []


def test_ac1_prefixless_aux_refused():
    arms = [dict(AUX_ARMS[0]), dict(AUX_ARMS[1], aux_models=[{"model": "qwen2-coder"}])]
    with pytest.raises(AuxModelError) as exc:
        _spec(arms=arms)
    assert "qwen2-coder" in str(exc.value)


def test_ac1_duplicate_declared_model_refused():
    arms = [
        dict(AUX_ARMS[0]),
        dict(
            AUX_ARMS[1],
            aux_models=[{"model": "meta/llama-3-70b-instruct-20240620"}],
        ),
    ]
    with pytest.raises(AuxModelError) as exc:
        _spec(arms=arms)
    assert "duplicate" in str(exc.value)


def test_ac1_malformed_aux_cutoff_refused_at_load():
    arms = [
        dict(AUX_ARMS[0]),
        dict(
            AUX_ARMS[1],
            aux_models=[{"model": "qwen/qwen2-coder-32b-20240901",
                         "training_cutoff": "not-a-date"}],
        ),
    ]
    with pytest.raises(Exception) as exc:
        _spec(arms=arms)
    assert "training_cutoff" in str(exc.value)


# --- AC-2: blinding completeness -------------------------------------------
def test_ac2_aux_ids_are_blinding_canaries():
    from harness.blind.core import arm_canaries, identity_pattern_list

    spec = _spec(arms=AUX_ARMS)
    canaries = arm_canaries(spec.arms)
    assert "qwen/qwen2-coder-32b-20240901" in canaries
    # the single scrub codepath kills the aux id exactly as a primary id
    patterns = identity_pattern_list(canaries)
    scrubbed, n = patterns.scrub(
        "routing subtask to qwen/qwen2-coder-32b-20240901 for edits"
    )
    assert n >= 1 and "qwen/qwen2-coder-32b-20240901" not in scrubbed


# --- AC-3: vendor overlap over the union ------------------------------------
def test_ac3_overlap_over_vendor_union():
    from harness.analyze.confounds import judge_vendor_overlap

    # judge is google; only the treatment's AUX model shares that vendor
    arms = [
        dict(AUX_ARMS[0]),
        dict(
            AUX_ARMS[1],
            aux_models=[{"model": "google/gemma-2-27b-20240627"}],
        ),
    ]
    overlap = judge_vendor_overlap(_spec(arms=arms))
    assert overlap.overlap is True
    assert overlap.overlapping_arms == ["treatment"]
    assert overlap.overlapping_models == {
        "treatment": ["google/gemma-2-27b-20240627"]
    }
    assert overlap.arm_vendor_sets["treatment"] == ["google", "meta"]
    # no aux overlap ⇒ clean, exactly as before
    assert judge_vendor_overlap(_spec(arms=AUX_ARMS)).overlap is False


# --- AC-4: contamination honesty --------------------------------------------
def test_ac4_latest_cutoff_bounds_clean():
    from harness.contamination.dating import ContaminationStatus, cutoff_status, effective_cutoff

    cutoffs = ["2024-06-01T00:00:00Z", "2024-09-01T00:00:00Z"]
    assert effective_cutoff(cutoffs) == "2024-09-01T00:00:00Z"
    eff = effective_cutoff(cutoffs)
    # created between the two cutoffs: the newest sub-model could have
    # memorized it — unknown, never clean
    assert cutoff_status("2024-07-15T00:00:00Z", eff) is ContaminationStatus.UNKNOWN
    # created after every declared cutoff: clean
    assert (
        cutoff_status("2024-10-01T00:00:00Z", eff)
        is ContaminationStatus.CLEAN_BY_DATE
    )


def test_ac4_missing_aux_cutoff_is_unknown(tmp_path):
    from types import SimpleNamespace

    from harness.contamination.dating import effective_cutoff
    from harness.contamination.summary import contamination_summary
    from tests.fixtures.builders import seed_trial_and_grade

    assert effective_cutoff(["2024-06-01T00:00:00Z", None]) is None

    arms = [
        dict(AUX_ARMS[0]),
        dict(AUX_ARMS[1], aux_models=[{"model": "qwen/qwen2-coder-32b-20240901"}]),
    ]  # aux carries NO cutoff; primary does
    spec, _, ledger = locked_experiment(tmp_path, arms=arms)
    ctx = fixed_ctx()
    seed_trial_and_grade(ledger, ctx, trial_id="t-1", task_id="task-1", arm="control")
    seed_trial_and_grade(ledger, ctx, trial_id="t-2", task_id="task-1", arm="treatment")
    manifest = SimpleNamespace(
        tasks=[SimpleNamespace(task_id="task-1", created_at="2024-12-01T00:00:00Z")]
    )
    summary = contamination_summary(ledger, spec, manifest=manifest)
    # control (single model, dated): clean; treatment (aux cutoff unknown): unknown
    assert summary["per_arm"]["control"]["clean_by_date"] == 1
    assert summary["per_arm"]["treatment"]["unknown"] == 1
    assert summary["per_arm"]["treatment"]["clean_by_date"] == 0
    # per-model breakdown makes the aggregation auditable: the primary is
    # individually clean; the undated aux is what drags the arm to unknown
    per_model = summary["per_arm"]["treatment"]["per_model"]
    assert per_model["meta/llama-3-70b-instruct-20240620"]["clean_by_date"] == 1
    assert per_model["qwen/qwen2-coder-32b-20240901"]["unknown"] == 1


# --- AC-5: mixed-vendor comparability ----------------------------------------
def test_ac5_mixed_vendor_arm_incomparable(tmp_path):
    from harness.analyze.report import _secondary_metrics

    spec, _, ledger = locked_experiment(tmp_path, arms=AUX_ARMS)
    sm = _secondary_metrics(ledger, spec)
    assert sm["cross_vendor"] is True
    assert sm["mixed_vendor_arms"] == ["treatment"]
    assert sm["arm_vendor_sets"]["treatment"] == ["meta", "qwen"]
    assert sm["vendor_incomparable_fields"]  # raw token fields excluded


def test_ac5_same_vendor_arms_stay_comparable(tmp_path):
    from harness.analyze.report import _secondary_metrics

    arms = [
        {"name": "control", "platform": "claude_code",
         "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
        {"name": "treatment", "platform": "generic",
         "model": "anthropic/claude-3-5-haiku-20241022",
         "aux_models": [{"model": "anthropic/claude-3-opus-20240229"}],
         "payload": {}},
    ]
    spec, _, ledger = locked_experiment(tmp_path / "same", arms=arms)
    sm = _secondary_metrics(ledger, spec)
    assert sm["cross_vendor"] is False
    assert sm["mixed_vendor_arms"] == []
    assert sm["vendor_incomparable_fields"] == []


# --- AC-6: declared-hosts egress attestation ---------------------------------
HOSTED_ARMS = [
    dict(
        AUX_ARMS[0],
        model_hosts={"anthropic/claude-3-5-sonnet-20241022": ["api.anthropic.com"]},
    ),
    dict(
        AUX_ARMS[1],
        aux_models=[{"model": "qwen/qwen2-coder-32b-20240901"}],
        model_hosts={
            "meta/llama-3-70b-instruct-20240620": ["llama.internal.example"],
            "qwen/qwen2-coder-32b-20240901": ["openrouter.ai"],
        },
    ),
]


def test_ac6_allowlist_derived_from_spec(tmp_path):
    from harness.run.egress import spec_allowlist
    from harness.run.settings import load_run_settings

    spec = _spec(arms=HOSTED_ARMS, infra_hosts=["pypi.org"])
    assert spec_allowlist(spec) == [
        "api.anthropic.com", "llama.internal.example", "openrouter.ai", "pypi.org",
    ]
    (tmp_path / "run.config.yaml").write_text(
        "proxy:\n  url: http://proxy:3128\n", encoding="utf-8"
    )
    settings = load_run_settings(tmp_path, env={}, spec=spec)
    assert settings.proxy.allowlist == spec_allowlist(spec)
    assert settings.proxy.infra_hosts == ["pypi.org"]


def test_ac6_runtime_allowlist_conflicts_with_spec_hosts(tmp_path):
    from harness.run.settings import load_run_settings

    spec = _spec(arms=HOSTED_ARMS, infra_hosts=["pypi.org"])
    (tmp_path / "run.config.yaml").write_text(
        "proxy:\n  url: http://proxy:3128\n  allowlist: [api.evil.example]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_run_settings(tmp_path, env={}, spec=spec)
    assert "pre-registers" in str(exc.value)


def test_ac6_partial_host_declaration_refused():
    # Mixed declaration: arm A declares hosts, arm B doesn't — the derived
    # allowlist would deny B's model API on every trial, a systematic bias.
    arms = [dict(HOSTED_ARMS[0]), dict(AUX_ARMS[1])]
    with pytest.raises(ModelHostsError) as exc:
        _spec(arms=arms)
    assert "treatment" in str(exc.value)
    # infra-only declaration: the derived allowlist would contain registries
    # but no model-API host at all, denying BOTH arms' provider calls.
    with pytest.raises(ModelHostsError):
        _spec(arms=AUX_ARMS, infra_hosts=["pypi.org"])


def test_ac6_infra_hosts_empty_entry_refused():
    from harness.schema.errors import InfraHostsError

    # an empty host suffix-matches every trailing-dot FQDN (endswith("."))
    with pytest.raises(InfraHostsError):
        _spec(arms=HOSTED_ARMS, infra_hosts=["", "pypi.org"])
    with pytest.raises(InfraHostsError):
        _spec(arms=HOSTED_ARMS, infra_hosts=["  "])


def test_ac6_declared_hosts_require_proxy_config(tmp_path):
    from harness.run.settings import load_run_settings

    spec = _spec(arms=HOSTED_ARMS, infra_hosts=["pypi.org"])
    # shape 1: no run.config.yaml at all — the pre-registered egress contract
    # must refuse loudly, never silently run unenforced
    with pytest.raises(ValueError) as exc:
        load_run_settings(tmp_path, env={}, spec=spec)
    assert "pre-registers" in str(exc.value)
    # shape 2: run.config.yaml present but no proxy block
    (tmp_path / "run.config.yaml").write_text("quotas: {cpus: 1}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_run_settings(tmp_path, env={}, spec=spec)
    # a spec declaring NO hosts keeps the pre-EVAL-20 behavior exactly
    plain = _spec()
    assert load_run_settings(tmp_path, env={}, spec=plain).proxy is None


def test_ac2_duck_typed_aux_entries_still_canaried():
    from types import SimpleNamespace

    from harness.blind.core import arm_canaries

    # the docstring invites duck-typing (no schema import): raw-dict aux
    # entries must still contribute canaries, never silently vanish
    arm = SimpleNamespace(
        name="treatment", platform="generic",
        model="meta/llama-3-70b-instruct-20240620",
        aux_models=[{"model": "qwen/qwen2-coder-32b-20240901"}],
    )
    assert "qwen/qwen2-coder-32b-20240901" in arm_canaries([arm])
    # an aux entry with no readable model id fails loudly — a silent skip
    # would be a blinding breach
    bad = SimpleNamespace(
        name="t", platform="generic", model="a/b-1234", aux_models=[{"oops": "x"}]
    )
    with pytest.raises(ValueError):
        arm_canaries([bad])


def test_ac6_model_hosts_key_must_be_declared():
    arms = [
        dict(AUX_ARMS[0], model_hosts={"openai/gpt-4o-2024-08-06": ["api.openai.com"]}),
        dict(AUX_ARMS[1]),
    ]
    with pytest.raises(ModelHostsError) as exc:
        _spec(arms=arms)
    assert "openai/gpt-4o-2024-08-06" in str(exc.value)


def _run_hosted_trial(tmp_path, *, egress_attempts):
    from harness.run.engines.fake import FakeEngine
    from harness.run.seam import run_trial
    from harness.run.types import ProxyConfig, RunConfig, Task

    arm = Arm.model_validate(HOSTED_ARMS[1])
    proxy = ProxyConfig(
        allowlist=[
            "api.anthropic.com", "llama.internal.example", "openrouter.ai", "pypi.org",
        ],
        proxy_url="http://proxy:3128",
        infra_hosts=["pypi.org"],
    )
    task = Task(
        id="t1", prompt="p", fake_behavior={"egress_attempts": egress_attempts}
    )
    return run_trial(
        task, arm, tmp_path / "ws", RunConfig(engine=FakeEngine(), proxy=proxy)
    )


def test_ac6_undeclared_model_egress_flag(tmp_path):
    # api.anthropic.com is allowed (the OTHER arm declared it) but attributable
    # to none of THIS arm's models and not infra — the sharpest violation shape
    rec = _run_hosted_trial(
        tmp_path,
        egress_attempts=["llama.internal.example", "pypi.org", "api.anthropic.com"],
    )
    assert rec.flags.undeclared_model_egress == ["api.anthropic.com"]
    assert rec.flags.egress_violation is False  # allowed host: not a violation


def test_ac6_declared_and_infra_hosts_raise_nothing(tmp_path):
    rec = _run_hosted_trial(
        tmp_path, egress_attempts=["openrouter.ai", "pypi.org"]
    )
    assert getattr(rec.flags, "undeclared_model_egress", None) is None


def test_ac6_flag_rides_never_gates(tmp_path):
    # advisory: the trial stays completed; the flag is data on the record
    rec = _run_hosted_trial(tmp_path, egress_attempts=["api.anthropic.com"])
    assert rec.outcome.value == "completed"
    assert rec.flags.undeclared_model_egress == ["api.anthropic.com"]
