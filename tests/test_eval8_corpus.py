"""EVAL-8 — corpus tooling: import, subset, mine, admission, boundary, versioning."""

from __future__ import annotations

import json

import pytest

from harness.corpus.admit import (
    BaselinePrerequisiteError,
    CurationRequiredError,
    admit_task,
)
from harness.corpus.mine import MergeRequest, MRFile, is_test_path, mine_mr
from harness.corpus.public import DirectorySource, import_terminal_bench
from pydantic import ValidationError

from harness.corpus.registry import (
    BoundaryViolationError,
    CorpusManifest,
    CorpusMutationError,
    Dataset,
    TaskEntry,
)
from harness.corpus.stratify import calibration_subset
from harness.ledger import events
from harness.ledger.events import record_curation_approval, record_flake_baseline
from tests.fixtures.builders import fixed_ctx

# A fixed test curator keypair + keyring (Ed25519; deterministic signing).
# D-P7-3: the keyring binds approver id -> public key. The default approver
# "curator" and the named "curator-alice" both hold the same fixed test key.
_CURATOR_PRIV = "57d8af6bd26b16f1f558e600e70fb2a40a5349804c864b3513b12015dc155556"
_CURATOR_PUB = "54f22d27057d6c0a336de3f2d0df143546f31591c169072e90f18f651e49e148"
_KEYRING = {"curator": _CURATOR_PUB, "curator-alice": _CURATOR_PUB}


def _approve(ledger, ctx, candidate_id, task_sha, *, approver="curator", priv=_CURATOR_PRIV):
    """Sign + record a curation_approval [D-P4-3]."""
    from harness.corpus.attestation import sign_approval

    sig, pk = sign_approval(priv, candidate_id=candidate_id, task_sha=task_sha, approver=approver)
    record_curation_approval(
        ledger, ctx, candidate_id=candidate_id, task_sha=task_sha, approver=approver,
        signature=sig, signer_public_key=pk,
    )
    return pk


def _write_dataset(root, n=6):
    """Fabricate a small harbor-format dataset dir with category metadata."""
    root.mkdir(parents=True, exist_ok=True)
    cats = ["io", "io", "parsing", "parsing", "net", "net"]
    for i in range(n):
        (root / f"task-{i}.json").write_text(
            json.dumps({"id": f"task-{i}", "prompt": f"do thing {i}", "harbor": True}),
            encoding="utf-8",
        )
        (root / f"task-{i}.meta.json").write_text(
            json.dumps({"category": cats[i % len(cats)], "difficulty": "medium"}),
            encoding="utf-8",
        )
    return root


# --- AC-1: public import + idempotency --------------------------------------
def test_ac1_public_import_manifest(tmp_path):
    src = _write_dataset(tmp_path / "ds")
    cache = tmp_path / "cache"
    m1 = import_terminal_bench(DirectorySource(src), cache, dataset_version="2.0")
    assert m1.kind == "public"
    assert m1.dataset == Dataset(name="terminal-bench", version="2.0")
    assert len(m1.tasks) == 6
    assert all(t.format == "harbor" for t in m1.tasks)
    manifest_bytes = (cache / "manifest.json").read_bytes()

    # Re-import against the same dataset version is byte-identical: no duplicates,
    # no sha churn.
    m2 = import_terminal_bench(DirectorySource(src), cache, dataset_version="2.0")
    assert m2.to_json() == m1.to_json()
    assert (cache / "manifest.json").read_bytes() == manifest_bytes
    assert m1.task_shas() == m2.task_shas()


def test_reimport_preserves_calibration(tmp_path):
    """CO-3: a byte-identical re-import must not wipe recorded calibration
    (reproduced: full-run-validated -> none)."""
    src = _write_dataset(tmp_path / "ds")
    cache = tmp_path / "cache"
    m1 = import_terminal_bench(DirectorySource(src), cache)
    m1.record_calibration_run({"full": True}, kind="full")
    m1.save(cache / "manifest.json")
    assert m1.calibration.status == "full-run-validated"

    m2 = import_terminal_bench(DirectorySource(src), cache)  # same semver, same content
    assert m2.calibration.status == "full-run-validated"


def test_m12_reimport_preserves_quarantine(tmp_path):
    """PRA-M12: a same-semver re-import must not silently revert a quarantined
    task to `admitted` (which would re-enable the run scheduler + official fence
    for it). Per-task recorded state is carried for unchanged shas."""
    src = _write_dataset(tmp_path / "ds")
    cache = tmp_path / "cache"
    m1 = import_terminal_bench(DirectorySource(src), cache)
    tid = m1.tasks[0].task_id
    m1.tasks[0].status = "quarantined"
    m1.tasks[0].baseline_ref = "b-pinned"
    m1.save(cache / "manifest.json")
    assert m1.is_schedulable(tid) is False

    m2 = import_terminal_bench(DirectorySource(src), cache)  # same semver, same content
    t2 = m2.task(tid)
    assert t2.status == "quarantined"  # not silently re-admitted
    assert t2.baseline_ref == "b-pinned"
    assert m2.is_schedulable(tid) is False


def test_reimport_same_semver_mutation_refused(tmp_path):
    """CO-3: mutating task content without a semver bump is refused, not a silent
    cache rewrite."""
    src = _write_dataset(tmp_path / "ds")
    cache = tmp_path / "cache"
    import_terminal_bench(DirectorySource(src), cache)
    cached = (cache / "tasks" / "task-0.json").read_text(encoding="utf-8")
    (src / "task-0.json").write_text(
        json.dumps({"id": "task-0", "prompt": "MUTATED", "harbor": True}), encoding="utf-8"
    )
    with pytest.raises(CorpusMutationError):
        import_terminal_bench(DirectorySource(src), cache)
    # the refusal happened before any cache write — the blob is unchanged
    assert (cache / "tasks" / "task-0.json").read_text(encoding="utf-8") == cached


# --- AC-2: stratified selection, calibration status, official gate ----------
def test_ac2_stratified_selection(tmp_path):
    src = _write_dataset(tmp_path / "ds", n=6)
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")

    a = calibration_subset(manifest, seed=1234, target_size=3, stratum_key="category")
    # reproducible for a seed
    manifest2 = import_terminal_bench(DirectorySource(src), tmp_path / "cache2")
    b = calibration_subset(manifest2, seed=1234, target_size=3, stratum_key="category")
    assert a.task_ids == b.task_ids
    # a different seed generally yields a different draw
    c = calibration_subset(manifest2, seed=9999, target_size=3, stratum_key="category")
    assert a.strata["stratum_key"] == "category"
    # proportional allocation covers strata; 3 chosen from 3 strata of 2 each ⇒ 1 each
    assert len(a.task_ids) == 3
    assert sum(a.strata["allocation"].values()) == 3
    _ = c  # a different seed is exercised; equality is not asserted (may collide)


def test_ac2_calibration_status(tmp_path):
    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    manifest.calibration.status = "none"
    assert manifest.official_ready is False

    calibration_subset(manifest, seed=1, target_size=3)
    # selection alone does not validate
    assert manifest.calibration.status == "none"

    manifest.record_calibration_run({"anchor_delta": 0.02}, kind="subset")
    assert manifest.calibration.status == "subset-validated"
    assert manifest.official_ready is False

    manifest.record_calibration_run({"full": True}, kind="full")
    assert manifest.calibration.status == "full-run-validated"
    assert manifest.official_ready is True


def test_ac2_official_requires_full(tmp_path):
    # The manifest field the EVAL-6 official-render path checks.
    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    manifest.calibration.status = "subset-validated"
    assert manifest.official_ready is False
    manifest.calibration.status = "full-run-validated"
    assert manifest.official_ready is True


# --- AC-3: mining -----------------------------------------------------------
def test_ac3_mine_candidate():
    assert is_test_path("tests/test_foo.py")
    assert is_test_path("pkg/foo_test.go")
    assert not is_test_path("src/foo.py")

    mr = MergeRequest(
        parent_sha="abc123" * 6 + "dead",
        files=[
            MRFile(path="src/feature.py", change="added", content="def f(): ..."),
            MRFile(path="tests/test_feature.py", change="added", content="def test_f(): ..."),
            MRFile(path="tests/test_existing.py", change="modified", content="..."),
        ],
    )
    cand = mine_mr(mr, ticket_text="Implement feature X per the ticket.")
    assert cand.workspace_ref == mr.parent_sha
    assert cand.prompt == "Implement feature X per the ticket."
    assert cand.status == "pending-curation"
    # holdouts = added test files only (not the modified one, not the src file)
    assert [h["path"] for h in cand.holdouts] == ["tests/test_feature.py"]


# --- AC-4: admission gate ---------------------------------------------------
def _pending_manifest(candidate_id="cand-1", sha="s" * 64, miner="miner-bot"):
    # miner defaults to a recorded id distinct from the "curator" approver, so
    # admission's approver≠miner bar is satisfiable (a task with no recorded miner
    # is refused — see test_dp4_3_admit_refuses_unrecorded_miner).
    return CorpusManifest(
        corpus_id="internal-koala",
        semver="1.0.0",
        kind="internal",
        boundary_path="/tmp/koala-boundary",
        tasks=[TaskEntry(task_id=candidate_id, sha=sha, status="pending-curation", miner=miner)],
    )


# a second keypair — a curator NOT in the authorized keyring / a miner's key
_OTHER_PRIV = "2fee083a79762784ce9b829d84f2d277287350999faddea81d75dc862367c726"
_OTHER_PUB = "86c17be71e223512eca950d661adb6004296452e26c13c9eb8718e4494e29db7"


def _clean_baseline(ledger, ctx, sha="s" * 64):
    record_flake_baseline(ledger, ctx, task_id="cand-1", task_sha=sha, k=5,
                          results=[{"run": i, "passed": True} for i in range(5)],
                          verdict="clean")


def test_dp4_3_admit_requires_authorized_signature(tmp_path):
    """A valid signature by an authorized, non-miner curator admits [D-P4-3]."""
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest(miner="miner-bob")
    _approve(ledger, ctx, "cand-1", "s" * 64, approver="curator-alice")
    _clean_baseline(ledger, ctx)
    task = admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                      baseline_ref="b1", keyring=_KEYRING)
    assert task.status == "admitted"


def test_dp4_3_self_approval_refused(tmp_path):
    """The miner cannot approve their own task [CO-7]."""
    from harness.corpus.admit import SelfApprovalError

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest(miner="curator-alice")  # miner == approver below
    _approve(ledger, ctx, "cand-1", "s" * 64, approver="curator-alice")
    _clean_baseline(ledger, ctx)
    with pytest.raises(SelfApprovalError):
        admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                   baseline_ref="b1", keyring=_KEYRING)
    assert manifest.is_schedulable("cand-1") is False


def test_dp4_3_admit_refuses_unrecorded_miner(tmp_path):
    """A candidate with no recorded miner cannot have the approver≠miner bar
    verified, so admission is refused rather than silently skipping it [CO-7]."""
    from harness.corpus.admit import SelfApprovalError

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest(miner=None)  # miner never recorded
    _approve(ledger, ctx, "cand-1", "s" * 64, approver="curator-alice")
    _clean_baseline(ledger, ctx)
    with pytest.raises(SelfApprovalError):
        admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                   baseline_ref="b1", keyring=_KEYRING)


def test_dp4_3_off_keyring_signer_refused(tmp_path):
    """A signature by a key not in the authorized keyring is refused — a
    self-generated key cannot launder an approval [D-P4-3]."""
    from harness.corpus.admit import UnauthorizedCuratorError

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest(miner="miner-bob")
    # signed by _OTHER_PRIV, whose public key is NOT in _KEYRING
    _approve(ledger, ctx, "cand-1", "s" * 64, approver="curator-mallory", priv=_OTHER_PRIV)
    _clean_baseline(ledger, ctx)
    with pytest.raises(UnauthorizedCuratorError):
        admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                   baseline_ref="b1", keyring=_KEYRING)


def test_co7_relabeled_self_approval_refused(tmp_path):
    """CO-7 / D-P7-3 probe: the miner holds an authorized key (as approver 'bob')
    and self-approves by RELABELING the approver to another registered curator
    ('alice'), signing with his own key. Old admission (verify against the
    self-attested key) accepted this; identity-bound admission refuses it because
    bob's signature does not verify under alice's registered key."""
    from harness.corpus.admit import AttestationError
    from harness.corpus.attestation import sign_approval

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest(miner="bob")
    keyring = {"alice": _CURATOR_PUB, "bob": _OTHER_PUB}  # both authorized approvers
    # bob signs an approval LABELED approver="alice" using his OWN key (_OTHER_PRIV)
    sig, pk = sign_approval(_OTHER_PRIV, candidate_id="cand-1", task_sha="s" * 64,
                            approver="alice")
    assert pk == _OTHER_PUB  # signed with bob's key
    record_curation_approval(ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                             approver="alice", signature=sig, signer_public_key=pk)
    _clean_baseline(ledger, ctx)
    with pytest.raises(AttestationError):
        admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                   baseline_ref="b1", keyring=keyring)


def test_dp7_3_legacy_list_keyring_refused(tmp_path):
    """A pre-Phase-7 list-format keyring is refused with a migration error."""
    from harness.corpus.attestation import KeyringFormatError, load_keyring

    kr = tmp_path / "keyring.json"
    kr.write_text(json.dumps([_CURATOR_PUB]), encoding="utf-8")
    with pytest.raises(KeyringFormatError):
        load_keyring(kr)


def test_dp4_3_tampered_signature_refused(tmp_path):
    """An approval whose signed payload is altered fails verification [D-P4-3]."""
    from harness.corpus.admit import AttestationError
    from harness.corpus.attestation import sign_approval

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest(miner="miner-bob")
    # sign for a DIFFERENT sha, then record it against "s"*64 -> signature won't verify
    sig, pk = sign_approval(_CURATOR_PRIV, candidate_id="cand-1", task_sha="x" * 64,
                            approver="curator-alice")
    record_curation_approval(ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                             approver="curator-alice", signature=sig, signer_public_key=pk)
    _clean_baseline(ledger, ctx)
    with pytest.raises(AttestationError):
        admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                   baseline_ref="b1", keyring=_KEYRING)


def test_ac4_curation_required(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest()
    # no curation_approval ⇒ refused
    with pytest.raises(CurationRequiredError):
        admit_task(
            manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
            baseline_ref="b1", keyring=_KEYRING,
        )
    assert manifest.is_schedulable("cand-1") is False


def test_ac4_baseline_prereq(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest()
    # approved but no clean baseline ⇒ refused
    _approve(ledger, ctx, "cand-1", "s" * 64)
    with pytest.raises(BaselinePrerequisiteError):
        admit_task(
            manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
            baseline_ref="b1", keyring=_KEYRING,
        )

    # add a clean baseline for the sha ⇒ admitted + schedulable
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha="s" * 64, k=5,
        results=[{"run": i, "passed": True} for i in range(5)], verdict="clean",
    )
    task = admit_task(
        manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
        baseline_ref="b1", keyring=_KEYRING,
    )
    assert task.status == "admitted"
    assert manifest.is_schedulable("cand-1") is True


def test_admit_refuses_tampered_chain(tmp_path):
    """CO-5/PL-6: admission reads its two preconditions from the ledger; it must
    verify the hash chain first, so a hand-forged ledger cannot admit a task.
    """
    from harness.ledger.chain import canonical_line
    from harness.ledger.query import ChainIntegrityError

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest()
    _approve(ledger, ctx, "cand-1", "s" * 64)
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha="s" * 64, k=5,
        results=[{"run": i, "passed": True} for i in range(5)], verdict="clean",
    )
    # tamper the approval line's *unchecked* approver field: the admission
    # preconditions still match by (candidate_id, task_sha), but the byte change
    # breaks the successor baseline line's prev_hash.
    lines = ledger.read_text(encoding="utf-8").splitlines()
    approval = json.loads(lines[0])
    assert approval["event"] == "curation_approval"
    approval["approver"] = "attacker"
    lines[0] = canonical_line(approval)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ChainIntegrityError):
        admit_task(
            manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
            baseline_ref="b1", keyring=_KEYRING,
        )
    assert manifest.is_schedulable("cand-1") is False


def test_ac4_baseline_must_be_clean(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest()
    _approve(ledger, ctx, "cand-1", "s" * 64)
    # a quarantined baseline is not a clean baseline
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha="s" * 64, k=5,
        results=[{"run": 0, "passed": False}], verdict="quarantined",
    )
    with pytest.raises(BaselinePrerequisiteError):
        admit_task(
            manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
            baseline_ref="b1", keyring=_KEYRING,
        )


# --- AC-5: boundary enforcement ---------------------------------------------
def test_ac5_boundary_enforced(tmp_path):
    from harness.corpus.registry import INSTRUMENT_ROOT

    # a path inside the instrument repo is structurally refused
    inside = CorpusManifest(
        corpus_id="internal-koala",
        semver="1.0.0",
        kind="internal",
        boundary_path=str(INSTRUMENT_ROOT / "corpora"),
        tasks=[],
    )
    with pytest.raises(BoundaryViolationError):
        inside.assert_boundary()

    # an internal corpus with no boundary is refused
    none = CorpusManifest(corpus_id="x", semver="1.0.0", kind="internal", tasks=[])
    with pytest.raises(BoundaryViolationError):
        none.assert_boundary()

    # a path outside the repo is fine
    ok = CorpusManifest(
        corpus_id="x", semver="1.0.0", kind="internal",
        boundary_path=str(tmp_path / "koala"), tasks=[],
    )
    ok.assert_boundary()  # does not raise


# --- §7.2 fail-closed sweep (CO-1/4/6/9) ------------------------------------
def test_co6_task_id_traversal_refused():
    # a registry-supplied task_id that would escape the cache dir is unrepresentable
    for bad in ("../../escaped", "a/b", "..", "/abs", "x\x00y"):
        with pytest.raises(ValidationError):
            TaskEntry(task_id=bad, sha="a" * 64)
    # a normal id is fine
    assert TaskEntry(task_id="task-1", sha="a" * 64).task_id == "task-1"


def test_co1_internal_save_into_instrument_repo_refused(tmp_path):
    from harness.corpus.registry import INSTRUMENT_ROOT, assert_outside_instrument

    m = CorpusManifest(corpus_id="internal-koala", semver="1.0.0", kind="internal",
                       boundary_path=str(tmp_path / "koala"), tasks=[])
    # saving an internal manifest inside the instrument repo is refused (write dest)
    with pytest.raises(BoundaryViolationError):
        m.save(INSTRUMENT_ROOT / "leaked_manifest.json")
    assert not (INSTRUMENT_ROOT / "leaked_manifest.json").exists()
    # outside the repo is fine
    m.save(tmp_path / "m.json")
    # the mine --out destination check refuses the repo too
    with pytest.raises(BoundaryViolationError):
        assert_outside_instrument(INSTRUMENT_ROOT / "candidate.json")


def test_co4_admission_ledgers_task_admitted(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _pending_manifest()
    _approve(ledger, ctx, "cand-1", "s" * 64)
    record_flake_baseline(ledger, ctx, task_id="cand-1", task_sha="s" * 64, k=5,
                          results=[{"run": i, "passed": True} for i in range(5)],
                          verdict="clean")
    from harness.ledger.query import find_events
    admit_task(manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
               baseline_ref="b1", keyring=_KEYRING)
    admitted = find_events(ledger, "task_admitted")
    assert len(admitted) == 1
    assert admitted[0]["candidate_id"] == "cand-1" and admitted[0]["task_sha"] == "s" * 64


def test_co4_calibration_run_ledgered(tmp_path):
    from harness.corpus.ledger_ops import ledger_calibration_run
    from harness.ledger.query import find_events

    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    ledger_calibration_run(ledger, ctx, manifest, {"anchor_delta": 0.01}, kind="subset")
    ev = find_events(ledger, "calibration_run")
    assert len(ev) == 1 and ev[0]["status"] == "subset-validated"
    ledger_calibration_run(ledger, ctx, manifest, {"full": True}, kind="full")
    ev = find_events(ledger, "calibration_run")
    assert len(ev) == 2 and ev[-1]["status"] == "full-run-validated"


def test_co9_subset_draw_ledgered(tmp_path):
    from harness.corpus.ledger_ops import ledger_subset_draw
    from harness.ledger.query import find_events

    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    subset = calibration_subset(manifest, seed=7, target_size=3, stratum_key="category")
    ledger = tmp_path / "l.ndjson"
    ledger_subset_draw(ledger, fixed_ctx(), manifest, subset)
    ev = find_events(ledger, "subset_draw")
    assert len(ev) == 1
    assert ev[0]["seed"] == 7 and ev[0]["task_ids"] == subset.task_ids


def test_co9_reimport_prunes_removed_task_blob(tmp_path):
    src = _write_dataset(tmp_path / "ds", n=6)
    cache = tmp_path / "cache"
    import_terminal_bench(DirectorySource(src), cache)
    assert (cache / "tasks" / "task-5.json").exists()
    # drop a task from the source and bump the semver (content set changed)
    (src / "task-5.json").unlink()
    (src / "task-5.meta.json").unlink()
    import_terminal_bench(DirectorySource(src), cache, semver="2.0.0")
    # the removed task's stale cache blob is pruned (no manifest/cache drift)
    assert not (cache / "tasks" / "task-5.json").exists()
    assert (cache / "tasks" / "task-0.json").exists()


# --- AC-6: versioning + provenance ------------------------------------------
def test_ac6_mutation_requires_bump():
    v1 = CorpusManifest(
        corpus_id="c", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id="t", sha="a" * 64, status="admitted")],
    )
    # mutate content, keep semver ⇒ refused
    v1_mut = CorpusManifest(
        corpus_id="c", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id="t", sha="b" * 64, status="admitted")],
    )
    with pytest.raises(CorpusMutationError):
        v1_mut.assert_valid_successor(v1)

    # bump semver ⇒ allowed, and the bump re-triggers the baseline
    v1_1 = CorpusManifest(
        corpus_id="c", semver="1.0.1", kind="public",
        tasks=[TaskEntry(task_id="t", sha="b" * 64, status="admitted", baseline_ref="old")],
    )
    v1_1.assert_valid_successor(v1)  # does not raise
    v1_1.retrigger_baselines(v1)
    changed = v1_1.task("t")
    assert changed.baseline_ref is None
    assert changed.status == "pending-curation"


def test_ac6_semver_cited():
    manifest = CorpusManifest(
        corpus_id="terminal-bench", semver="2.1.0", kind="public",
        tasks=[
            TaskEntry(task_id="t2", sha="2" * 64, status="admitted"),
            TaskEntry(task_id="t1", sha="1" * 64, status="admitted"),
        ],
    )
    ref = manifest.provenance_ref()
    assert ref["corpus_id"] == "terminal-bench"
    assert ref["semver"] == "2.1.0"
    # task shas sorted by id — the byte-reconstructible citation
    assert ref["task_shas"] == {"t1": "1" * 64, "t2": "2" * 64}


def test_ac_non_harbor_format_rejected():
    # format is a Literal["harbor"] ⇒ a non-harbor task is unrepresentable [D003].
    with pytest.raises(ValidationError):
        TaskEntry(task_id="t", sha="a" * 64, format="custom")


def test_manifest_roundtrip(tmp_path):
    src = _write_dataset(tmp_path / "ds")
    manifest = import_terminal_bench(DirectorySource(src), tmp_path / "cache")
    path = manifest.save(tmp_path / "m.json")
    loaded = CorpusManifest.load(path)
    assert loaded.to_json() == manifest.to_json()


def test_curation_approval_registered():
    assert "curation_approval" in events.REGISTERED_EVENTS


def test_m_o6_bump_reimport_carries_quarantine_and_never_a_stale_baseline(tmp_path):
    """F-M-O6: a semver bump previously rebuilt every entry fresh — silently
    reverting a quarantined task to schedulable and dropping valid baselines.
    Unchanged tasks now carry their state across a bump exactly like a
    same-semver re-import [PRA-M12]; a CHANGED task keeps the fresh state, so
    no stale baseline_ref ever rides a bump (AC-6, structurally)."""
    src = _write_dataset(tmp_path / "ds")
    cache = tmp_path / "cache"
    m1 = import_terminal_bench(DirectorySource(src), cache, semver="1.0.0")
    changed_id = m1.tasks[0].task_id
    kept_id = m1.tasks[1].task_id
    # operator records state on v1: one task quarantined, one baselined
    m1.task(kept_id).status = "quarantined"
    m1.task(changed_id).baseline_ref = "stale-ref"
    m1.save(cache / "manifest.json")

    (src / f"{changed_id}.json").write_text('{"mutated": true}', encoding="utf-8")
    m2 = import_terminal_bench(DirectorySource(src), cache, semver="1.1.0")
    assert m2.task(kept_id).status == "quarantined"          # carried: same sha
    assert m2.task(kept_id).sha == m1.task(kept_id).sha
    assert m2.task(changed_id).sha != m1.task(changed_id).sha  # changed content
    assert m2.task(changed_id).baseline_ref is None          # never rides the bump
    assert m2.task(changed_id).status == "admitted"          # public policy unchanged


# --- calibrate statistics moved out of the thin CLI [refactor 07 §3] --------
def test_realized_calibration_run_derives_run_from_grades(tmp_path):
    """The inline calibrate stats moved into a corpus function: it derives the
    calibration ``run`` record (mean holdout pass rate + task count) from the
    ledger's grades — the exact record the CLI/api used to inline."""
    from harness.corpus.ledger_ops import realized_calibration_run
    from tests.fixtures.builders import seed_trial_and_grade

    ledger = tmp_path / "ledger.ndjson"
    ctx = fixed_ctx(experiment_id="exp")
    seed_trial_and_grade(ledger, ctx, trial_id="t1", task_id="ta", arm="control", passed=True)
    seed_trial_and_grade(ledger, ctx, trial_id="t2", task_id="tb", arm="control", passed=False)
    assert realized_calibration_run(ledger, rho=0.3, kind="full") == {
        "p": 0.5, "rho": 0.3, "n_tasks": 2, "kind": "full",
    }


def test_realized_calibration_run_refuses_a_ledger_with_no_grades(tmp_path):
    """Fail loudly, never a silent p over zero tasks [fail loudly]."""
    from harness.corpus.ledger_ops import NoGradedTrialsError, realized_calibration_run

    empty = tmp_path / "empty.ndjson"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(NoGradedTrialsError, match="no graded trials"):
        realized_calibration_run(empty, rho=0.3, kind="subset")


# --- admit's two-phase persistence moved beside admit_task [refactor 07 §3] --
def _approved_baselined(ledger, ctx):
    """A pending candidate with every ledger precondition satisfied, so the
    persistence tests refuse/report for exactly the reason under test."""
    manifest = _pending_manifest()
    _approve(ledger, ctx, "cand-1", "s" * 64)
    record_flake_baseline(
        ledger, ctx, task_id="cand-1", task_sha="s" * 64, k=5,
        results=[{"run": i, "passed": True} for i in range(5)], verdict="clean",
    )
    return manifest


def test_admit_with_persistence_probes_destinations_before_ledgering(tmp_path):
    """PRA-M11 phase 1: a non-writable manifest destination refuses with NOTHING
    ledgered and the candidate still pending — every other precondition holds,
    so the refusal is exactly the destination probe."""
    import os as _os

    from harness.corpus.admit import AdmitDestinationError, admit_with_persistence
    from harness.ledger.query import find_events

    if _os.geteuid() == 0:  # pragma: no cover - root ignores mode bits
        pytest.skip("os.access(W_OK) cannot be denied to root")
    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _approved_baselined(ledger, ctx)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        with pytest.raises(AdmitDestinationError, match="not writable"):
            admit_with_persistence(
                manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
                baseline_ref="b1", keyring=_KEYRING, manifest_path=ro / "manifest.json",
            )
    finally:
        ro.chmod(0o755)
    assert find_events(ledger, "task_admitted") == []
    assert manifest.task("cand-1").status == "pending-curation"


def test_admit_with_persistence_reports_post_ledger_persist_failure(tmp_path):
    """PRA-M11 phase 2: a persistence failure AFTER the ledger write is returned
    as ``persist_error`` with the recovery hint — the admission is on the chain,
    never swallowed and never re-raised as if nothing was ledgered."""
    from harness.corpus.admit import admit_with_persistence
    from harness.ledger.query import find_events

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    manifest = _approved_baselined(ledger, ctx)
    # The parent is writable (the pre-ledger probe passes) but manifest.save
    # hits a DIRECTORY at the manifest path — an OSError only phase 2 can see.
    manifest_path = tmp_path / "manifest.json"
    manifest_path.mkdir()
    outcome = admit_with_persistence(
        manifest, ledger, ctx, candidate_id="cand-1", task_sha="s" * 64,
        baseline_ref="b1", keyring=_KEYRING, manifest_path=manifest_path,
    )
    assert len(find_events(ledger, "task_admitted")) == 1  # on the chain
    assert outcome.embedded_path is None
    assert outcome.persist_error is not None
    assert "The admission is on the chain" in outcome.persist_error
    assert str(manifest_path) in outcome.persist_error  # names the reconcile target
