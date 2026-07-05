"""Control-reuse bundle export [control-reuse plan, slice 3].

Exporting a control arm from a completed run yields a self-contained,
tamper-evident bundle: one cell per control trial (trial record + grade +
judged-diff snapshot), the control fingerprint, and source provenance.
"""

from __future__ import annotations

import pytest
import yaml

from harness.run.reuse import (
    ControlBundleError,
    bundle_sha,
    build_bundle,
    verify_bundle,
)
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade


def _source(tmp_path):
    """A locked experiment with a control arm, two tasks + holdouts, and seeded
    control trials/grades — a completed source run ready to export."""
    src = tmp_path / "src-exp"
    spec, spec_path, ledger = locked_experiment(src)
    (src / "tasks.yaml").write_text(
        yaml.safe_dump(
            {"tasks": [
                {"id": "t1", "prompt": "p1", "holdouts_dir": "holdouts/t1", "plugins": ["groundwork"]},
                {"id": "t2", "prompt": "p2", "holdouts_dir": "holdouts/t2"},
            ]}
        ),
        encoding="utf-8",
    )
    for tid, body in (("t1", "assert a"), ("t2", "assert b")):
        d = src / "holdouts" / tid
        d.mkdir(parents=True)
        (d / "holdout.json").write_text(body, encoding="utf-8")
    ctx = fixed_ctx(experiment_id="src-exp")
    for tid in ("t1", "t2"):
        seed_trial_and_grade(
            ledger, ctx, trial_id=f"tr-{tid}", task_id=tid, arm="control", passed=True
        )
    return src


def test_build_bundle_shape(tmp_path):
    bundle = build_bundle(_source(tmp_path), "control")
    assert bundle["control_arm"] == "control"
    assert bundle["source_experiment_id"] == "src-exp"
    assert [c["task_id"] for c in bundle["cells"]] == ["t1", "t2"]
    for cell in bundle["cells"]:
        assert cell["trial_record"]["arm"] == "control"
        assert cell["grade"]["binary_score"] is True
        assert "diff" in cell  # snapshot present (empty string when no live workspace)
    assert bundle["fingerprint"]["digest"]
    assert bundle["audit"]["engine"] == "fake"


def test_bundle_self_sha_is_tamper_evident(tmp_path):
    bundle = build_bundle(_source(tmp_path), "control")
    verify_bundle(bundle)  # clean bundle verifies
    assert bundle["bundle_sha256"] == bundle_sha(bundle)
    bundle["cells"][0]["grade"]["binary_score"] = False  # tamper
    with pytest.raises(ControlBundleError, match=r"does not match its contents"):
        verify_bundle(bundle)


def test_unknown_arm_refused(tmp_path):
    with pytest.raises(ControlBundleError, match=r"not declared"):
        build_bundle(_source(tmp_path), "nonesuch")


def test_arm_with_no_trials_refused(tmp_path):
    # 'treatment' is a declared arm but has no seeded trials — nothing to export
    with pytest.raises(ControlBundleError, match=r"no trials for control arm"):
        build_bundle(_source(tmp_path), "treatment")
