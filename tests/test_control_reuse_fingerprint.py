"""Control-reuse fingerprint + preflight gate [control-reuse plan, slice 1].

Behavior tests (plain-named, not AC-mapped): a clean control reuse matches, and
every gated component that drifts refuses reuse loudly, naming what moved.
"""

from __future__ import annotations

import os

import pytest

from harness.adapters.base import Quotas
from harness.corpus.commit import holdout_content_sha
from harness.run.control_reuse import (
    ControlReuseFingerprintError,
    assert_fingerprint_match,
    compute_fingerprint,
)
from harness.schema.experiment import Arm


def _arm(model="anthropic/claude-3-5-sonnet-20241022", **kw):
    return Arm(name="control", platform="claude_code", model=model, **kw)


def _tasks():
    return [
        {"id": "t1", "prompt": "solve it", "holdouts_dir": "holdouts/t1", "plugins": ["groundwork"]},
        {"id": "t2", "prompt": "and this", "holdouts_dir": "holdouts/t2"},
    ]


def _write_holdouts(exp_dir):
    for tid, body in (("t1", "assert x == 1"), ("t2", "assert y == 2")):
        d = exp_dir / "holdouts" / tid
        d.mkdir(parents=True)
        (d / "holdout.json").write_text(body, encoding="utf-8")


def _fingerprint(exp_dir, *, arm=None, tasks=None, repetitions=3, git_sha="abc123", **env):
    return compute_fingerprint(
        arm=arm or _arm(),
        task_dicts=tasks or _tasks(),
        experiment_dir=exp_dir,
        engine=env.get("engine", "fake"),
        quotas=env.get("quotas", Quotas(cpus=2.0, mem="4g")),
        proxy_allowlist=env.get("proxy_allowlist", ["api.anthropic.com"]),
        infra_hosts=env.get("infra_hosts", []),
        repetitions=repetitions,
        plugin_ids=env.get("plugin_ids", ["groundwork"]),
        instrument_git_sha=git_sha,
    )


def test_clean_match_passes(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path)
    current = _fingerprint(tmp_path)
    assert current["digest"] == recorded["digest"]
    assert_fingerprint_match(current, recorded)  # does not raise


def test_task_definition_drift_refuses(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path)
    changed = _tasks()
    changed[0]["prompt"] = "solve it differently"
    with pytest.raises(ControlReuseFingerprintError, match=r"task 't1' definition"):
        assert_fingerprint_match(_fingerprint(tmp_path, tasks=changed), recorded)


def test_holdout_byte_drift_refuses(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path)
    # mutate the holdout script bytes on disk — the coverage the lock omits
    (tmp_path / "holdouts" / "t1" / "holdout.json").write_text("assert x == 999", encoding="utf-8")
    with pytest.raises(ControlReuseFingerprintError, match=r"task 't1' holdout script bytes"):
        assert_fingerprint_match(_fingerprint(tmp_path), recorded)


def test_arm_drift_refuses(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path)
    other = _fingerprint(tmp_path, arm=_arm(model="anthropic/claude-3-opus-20240229"))
    with pytest.raises(ControlReuseFingerprintError, match=r"arm definition changed"):
        assert_fingerprint_match(other, recorded)


def test_env_drift_refuses(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path)
    other = _fingerprint(tmp_path, quotas=Quotas(cpus=4.0, mem="8g"))
    with pytest.raises(ControlReuseFingerprintError, match=r"operational environment"):
        assert_fingerprint_match(other, recorded)


def test_grader_version_drift_refuses(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path, git_sha="old-sha")
    other = _fingerprint(tmp_path, git_sha="new-sha")
    with pytest.raises(ControlReuseFingerprintError, match=r"grader changed"):
        assert_fingerprint_match(other, recorded)


def test_repetitions_drift_refuses(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path, repetitions=3)
    other = _fingerprint(tmp_path, repetitions=5)
    with pytest.raises(ControlReuseFingerprintError, match=r"repetitions changed"):
        assert_fingerprint_match(other, recorded)


def test_version_mismatch_refuses(tmp_path):
    _write_holdouts(tmp_path)
    recorded = _fingerprint(tmp_path)
    current = dict(recorded)
    current["version"] = recorded["version"] + 1
    with pytest.raises(ControlReuseFingerprintError, match=r"fingerprint version mismatch"):
        assert_fingerprint_match(current, recorded)


# --- the corpus holdout-hash helper ----------------------------------------
def test_holdout_content_sha_absent_dir_is_stable(tmp_path):
    missing = tmp_path / "nope"
    assert holdout_content_sha(missing) == holdout_content_sha(tmp_path / "also-nope")


def test_holdout_content_sha_tracks_bytes(tmp_path):
    d = tmp_path / "h"
    d.mkdir()
    (d / "a.json").write_text("one", encoding="utf-8")
    before = holdout_content_sha(d)
    (d / "a.json").write_text("two", encoding="utf-8")
    assert holdout_content_sha(d) != before


def test_holdout_content_sha_skips_symlink_escape(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("host bytes", encoding="utf-8")
    d = tmp_path / "h"
    d.mkdir()
    (d / "real.json").write_text("real", encoding="utf-8")
    only_real = holdout_content_sha(d)
    try:
        os.symlink(secret, d / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    # a planted symlink must not pull foreign bytes into the fingerprint
    assert holdout_content_sha(d) == only_real
