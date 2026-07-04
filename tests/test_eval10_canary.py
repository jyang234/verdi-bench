"""Contamination canaries: deterministic embed, hash-only publication [EVAL-10 AC-2]."""

from __future__ import annotations

import json

import pytest

from harness.contamination.canary import (
    CanaryError,
    derive_canary,
    embed_canary,
    hash_canary,
    strip_canary,
)
from harness.corpus.admit import admit_task
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.ledger.events import record_curation_approval, record_flake_baseline
from harness.review.scrub import ScrubError, assert_identity_free, blind_scrub
from tests.fixtures.builders import fixed_ctx

# The fixed test curator keypair the eval8 admission fixtures use [D-P4-3].
_CURATOR_PRIV = "57d8af6bd26b16f1f558e600e70fb2a40a5349804c864b3513b12015dc155556"
_CURATOR_PUB = "54f22d27057d6c0a336de3f2d0df143546f31591c169072e90f18f651e49e148"
_KEYRING = {"curator": _CURATOR_PUB}
_SHA = "a1" * 32


def _task_sha(content: dict) -> str:
    """The real content sha admit_task now verifies against (PRA-M11) — the
    defining-subset hash, matching Candidate.content_sha."""
    from harness.corpus.public import content_sha

    return content_sha({
        "workspace_ref": content.get("workspace_ref"),
        "prompt": content.get("prompt"),
        "holdouts": content.get("holdouts", []),
        "groundwork_rules": content.get("groundwork_rules"),
    })


def _admissible(tmp_path, sha=_SHA):
    """A pending candidate with a signed approval + clean baseline on the ledger."""
    from harness.corpus.attestation import sign_approval

    ledger = tmp_path / "ledger.ndjson"
    ctx = fixed_ctx()
    sig, pk = sign_approval(
        _CURATOR_PRIV, candidate_id="cand-1", task_sha=sha, approver="curator"
    )
    record_curation_approval(
        ledger, ctx, candidate_id="cand-1", task_sha=sha, approver="curator",
        signature=sig, signer_public_key=pk,
    )
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha=sha, k=5,
        results=[{"run": i, "passed": True} for i in range(5)], verdict="clean",
    )
    manifest = CorpusManifest(
        corpus_id="internal-k", semver="1.0.0", kind="internal",
        boundary_path="/tmp/k-boundary",
        tasks=[TaskEntry(task_id="cand-1", sha=sha, status="pending-curation",
                         miner="miner-bob")],
    )
    return manifest, ledger, ctx


def test_ac2_canary_deterministic_embed(tmp_path):
    """Admission derives the canary from ``task_sha`` (no randomness), embeds an
    inert marker, and records the manifest hash [AC-2]."""
    # derivation is a pure sub-hash: stable across calls, distinct across shas
    canary = derive_canary(_SHA)
    assert canary == derive_canary(_SHA)
    assert canary != derive_canary("b2" * 32)
    assert canary.startswith("VBCANARY-")

    # embedding is pure and inert: prompt gains the marker, nothing else changes
    content = {"prompt": "Fix the bug in foo().", "workspace_ref": "w" * 40}
    embedded = embed_canary(content, canary)
    assert content["prompt"] == "Fix the bug in foo()."  # input not mutated
    assert embedded["prompt"].startswith("Fix the bug in foo().")
    assert f"<!-- {canary} -->" in embedded["prompt"]
    assert embedded["workspace_ref"] == content["workspace_ref"]
    with pytest.raises(CanaryError, match="already embedded"):
        embed_canary(embedded, canary)
    with pytest.raises(CanaryError, match="prompt"):
        embed_canary({"workspace_ref": "w"}, canary)
    # strip is the exact inverse, so the probe can send the pre-embed content
    assert strip_canary(embedded["prompt"], canary) == content["prompt"]

    # admission given the content embeds + records the canary by hash. The
    # task_sha is the content's real sha (PRA-M11 verifies this), so the canary
    # is derived from it.
    sha_c = _task_sha(content)
    manifest, ledger, ctx = _admissible(tmp_path, sha=sha_c)
    task = admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha=sha_c,
                      baseline_ref="b1", keyring=_KEYRING,
                      candidate_content=dict(content))
    assert task.canary_sha256 == hash_canary(derive_canary(sha_c))


def test_admit_without_content_claims_no_canary(tmp_path):
    """Admission WITHOUT candidate content records no canary hash — claiming a
    canary that was never embedded would turn the probe's honest 'unprobed'
    into a false 'negative' [AC-2, review fix]."""
    manifest, ledger, ctx = _admissible(tmp_path)
    task = admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha=_SHA,
                      baseline_ref="b1", keyring=_KEYRING)
    assert task.canary_sha256 is None
    assert task.status == "admitted"


def test_admit_embed_failure_refuses_before_ledgering(tmp_path):
    """A failing embed refuses admission outright: no status flip, no
    task_admitted event — never a ledgered-but-unmarked tear [review fix]."""
    from harness.ledger.query import find_events

    content = {"no_prompt": "here"}
    sha_c = _task_sha(content)  # sha matches, so the embed (not the sha) is what fails
    manifest, ledger, ctx = _admissible(tmp_path, sha=sha_c)
    with pytest.raises(CanaryError, match="prompt"):
        admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha=sha_c,
                   baseline_ref="b1", keyring=_KEYRING,
                   candidate_content=content)
    assert manifest.task("cand-1").status == "pending-curation"
    assert find_events(ledger, "task_admitted") == []


def test_m11_admit_refuses_already_admitted(tmp_path):
    """PRA-M11: a second admit_task on an already-admitted candidate refuses,
    so a re-run (e.g. after a torn late-save) cannot append a second
    task_admitted event."""
    from harness.ledger.query import find_events

    manifest, ledger, ctx = _admissible(tmp_path)
    admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha=_SHA,
               baseline_ref="b1", keyring=_KEYRING)
    assert manifest.task("cand-1").status == "admitted"
    with pytest.raises(Exception, match="already admitted"):
        admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha=_SHA,
                   baseline_ref="b1", keyring=_KEYRING)
    assert len(find_events(ledger, "task_admitted")) == 1


def test_ac2_canary_never_published(tmp_path):
    """Canary values are secrets of the instrument: manifests and probe events
    carry sha256(canary) only, and a published surface containing a value fails
    the shared scrub property test [AC-2, constraint]."""
    from harness.contamination.probe import ProbeTask, run_memory_probe
    from harness.judge.providers.fake import FakeProvider
    from harness.schema.experiment import Arm

    content = {"prompt": "Fix the bug in foo()."}
    sha_c = _task_sha(content)
    canary = derive_canary(sha_c)

    # the manifest serialization carries the hash, never the value
    manifest, ledger, ctx = _admissible(tmp_path, sha=sha_c)
    admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha=sha_c,
               baseline_ref="b1", keyring=_KEYRING,
               candidate_content=dict(content))
    blob = manifest.to_json()
    assert canary not in blob
    assert hash_canary(canary) in blob

    # a probe whose fake model regurgitates the canary ledgers the flag — but
    # the ledger bytes never contain the value, only its hash
    probe_ledger = tmp_path / "probe-ledger.ndjson"
    arm = Arm(name="control", platform="claude_code",
              model="anthropic/claude-3-5-sonnet-20241022")
    run_memory_probe(
        probe_ledger, fixed_ctx(),
        arms=[arm],
        tasks=[ProbeTask(task_id="cand-1", task_sha=sha_c,
                         prompt="Fix the bug in foo().", has_canary=True)],
        provider=FakeProvider([f"...and then {canary} appeared"]),
    )
    ledger_bytes = probe_ledger.read_text(encoding="utf-8")
    assert canary not in ledger_bytes
    assert hash_canary(canary) in ledger_bytes

    # any render/packet surface containing a canary value fails the shared
    # scrub property test (one scrub mechanism, one list to extend [§7.4]) —
    # and the VBCANARY marker format is a BUILT-IN pattern, so every existing
    # scrub/assert surface catches it without per-experiment literals
    leaking_render = f"## Findings\n\nthe task said <!-- {canary} -->"
    with pytest.raises(ScrubError):
        assert_identity_free(leaking_render)  # no extra literals needed
    with pytest.raises(ScrubError):
        assert_identity_free(leaking_render, canaries=[canary])
    assert canary not in blind_scrub(leaking_render)
    assert_identity_free("## Findings\n\nclean text", canaries=[canary])
