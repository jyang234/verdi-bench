"""EVAL-17 — authoring surface: previews as pure reads, the lock as a ceremony.

AC map: preview purity (AC-1), the one-event ceremony incl. the underpowered
path and a browser drive (AC-2), post-lock immutability (AC-3), posture
(AC-4), commitment parity with bench plan (AC-5).
Spec: docs/design/specs/eval17.spec.md.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.author.server import DEFAULT_HOST, make_author_server
from harness.ledger.query import find_events, read_events
from harness.plan.interleave import derive_schedule, enumerate_trials
from harness.plan.power import AssumedVariance, mde_check
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.browser import drive
from tests.fixtures.servers import running_server

pytestmark = pytest.mark.browser

_QUICK = {"n_sim": 8, "n_boot": 40, "deltas": [0.2, 0.4]}

SPEC = """arms:
  - {name: control, platform: claude_code, model: anthropic/claude-3-5-sonnet-20241022, payload: {}}
  - {name: treatment, platform: codex, model: openai/gpt-4o-2024-08-06, payload: {}}
corpus: {id: demo, version: 1.0.0}
repetitions: 2
primary_metric: holdout_pass_rate
decision_rule: delta_holdout_pass_rate > 0
judge: {model: google/gemini-1.5-pro-002, rubric: rubrics/r.md, orders: both, temperature: 0}
seed: 99
cost_ceiling: {amount: 5.0, currency: USD}
"""
TASKS = "tasks:\n  - id: t1\n    prompt: p\n"
RUBRIC = "judge on correctness\n"


@contextmanager
def _serve(root: Path, **kwargs):
    srv = make_author_server(root, actor=kwargs.pop("actor", "tester"), port=0,
                             lock_kwargs=kwargs.pop("lock_kwargs", _QUICK), **kwargs)
    with running_server(srv) as base:
        yield base


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path) as resp:
        return json.loads(resp.read())


def _post(base: str, path: str, body: dict, *, headers: dict | None = None):
    # A real same-origin browser fetch carries Origin + application/json; the
    # CSRF guard (PRA-H2) requires both. Callers can override to exercise refusals.
    hdrs = {"Content-Type": "application/json", "Origin": base}
    hdrs.update(headers or {})
    hdrs = {k: v for k, v in hdrs.items() if v is not None}  # None => omit header
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers=hdrs,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _save_draft(base: str, name: str = "exp-a", spec: str = SPEC) -> None:
    code, _ = _post(base, "/api/draft", {"name": name, "files": {
        "experiment.yaml": spec, "tasks.yaml": TASKS, "rubrics/r.md": RUBRIC}})
    assert code == 200


def _digest(root: Path) -> list:
    return sorted(
        (str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*") if p.is_file()
    )


# --- AC-1: previews are pure reads over the saved bytes -----------------------------
def test_ac1_previews_pure_reads(tmp_path):
    with _serve(tmp_path) as base:
        _save_draft(base)
        draft = tmp_path / "exp-a"

        v = _get(base, "/api/validate?name=exp-a")
        assert v["spec"]["ok"] and v["tasks"] == {"ok": True, "count": 1, "ids": ["t1"]}
        # the sha previewed IS the sha of the saved bytes
        assert v["spec_sha256"] == hashlib.sha256(
            (draft / "experiment.yaml").read_bytes()
        ).hexdigest()

        # power preview equals mde_check for the same inputs [AC-1]
        spec = ExperimentSpec.from_yaml((draft / "experiment.yaml"))
        direct = mde_check(spec, AssumedVariance(), n_tasks=1,
                           n_sim=8, n_boot=40, deltas=[0.05, 0.1, 0.2, 0.3, 0.4, 0.5])
        served = _get(base, "/api/power?name=exp-a&quick=1")
        assert served["quick"] is True and served["mde"] == json.loads(json.dumps(direct))

        # schedule preview equals the deterministic derivation
        order = derive_schedule(spec.seed, enumerate_trials(["t1"], ["control", "treatment"], 2))
        sched = _get(base, "/api/schedule?name=exp-a")
        assert sched["total"] == 4
        assert sched["order"] == [
            {"task_id": t.task_id, "arm": t.arm, "repetition": t.repetition} for t in order
        ]

        # typed errors, named: a spec without a cost ceiling; tasks with dup ids
        _post(base, "/api/draft", {"name": "exp-bad", "files": {
            "experiment.yaml": "arms:\n  - {name: a, platform: p, model: m/x, payload: {}}\n"
                               "  - {name: b, platform: p, model: m/y, payload: {}}\n"
                               "corpus: {id: c, version: 1.0.0}\nrepetitions: 1\n"
                               "primary_metric: holdout_pass_rate\n"
                               "decision_rule: delta_holdout_pass_rate > 0\n"
                               "judge: {model: g/m, rubric: rubrics/r.md}\nseed: 1\n",
            "tasks.yaml": "tasks:\n  - id: t1\n  - id: t1\n"}})
        vb = _get(base, "/api/validate?name=exp-bad")
        assert vb["spec"]["ok"] is False
        assert vb["spec"]["error_class"] == "MissingCostCeilingError"
        assert vb["tasks"]["ok"] is False and "t1" in vb["tasks"]["error"]

        # purity: previews change nothing — no ledger exists, bytes identical
        before = _digest(tmp_path)
        for path in ("/api/validate?name=exp-a", "/api/power?name=exp-a&quick=1",
                     "/api/schedule?name=exp-a", "/api/sha?name=exp-a",
                     "/api/drafts", "/api/draft?name=exp-a"):
            _get(base, path)
        assert _digest(tmp_path) == before
        assert not (draft / "ledger.ndjson").exists()


# --- AC-2: the ceremony — one event, typed refusals, underpowered path ---------------
def test_ac2_lock_ceremony_one_event(tmp_path):
    with _serve(tmp_path, actor="ceremony-actor") as base:
        _save_draft(base)
        sha_preview = _get(base, "/api/sha?name=exp-a")["spec_sha256"]

        code, locked = _post(base, "/api/lock", {"name": "exp-a", "attested_by": "jyang"})
        assert code == 200 and locked["locked"] is True
        assert locked["spec_sha256"] == sha_preview  # byte fidelity end to end

        evs = read_events(tmp_path / "exp-a" / "ledger.ndjson")
        assert [e["event"] for e in evs] == ["experiment_locked"]  # exactly one
        lock = evs[0]
        assert lock["spec_sha256"] == sha_preview
        assert lock["provenance"]["actor"] == "ceremony-actor"  # launch-bound
        assert lock["attestation"]["attested_by"] == "jyang"    # explicit field

        # re-lock: the typed refusal, verbatim
        code, err = _post(base, "/api/lock", {"name": "exp-a", "attested_by": "jyang"})
        assert code == 409 and err["error_class"] == "AlreadyLockedError"
        # attestation is required, not defaulted
        _save_draft(base, name="exp-noattest")
        code, err = _post(base, "/api/lock", {"name": "exp-noattest"})
        assert code == 409 and "attested_by" in err["error"]

        # the underpowered path: refusal with the numbers, then the ledgered ack
        _save_draft(base, name="exp-under", spec=SPEC + "hypothesized_effect: 0.01\n")
        code, err = _post(base, "/api/lock", {"name": "exp-under", "attested_by": "jyang"})
        assert code == 409 and err["error_class"] == "UnderpoweredError"
        assert "0.01" in err["error"]  # the message carries the numbers
        code, ok = _post(base, "/api/lock", {"name": "exp-under", "attested_by": "jyang",
                                             "acknowledge_underpowered": True})
        assert code == 200
        under = read_events(tmp_path / "exp-under" / "ledger.ndjson")
        assert len(under) == 1  # the ack rides inline — still one event [PL-14]
        assert under[0]["acknowledged_underpowered"]["hypothesized_effect"] == 0.01


def test_ac2_page_ceremony_drive(tmp_path):
    """The same ceremony, driven through the actual page: template → edit →
    save → lock, ending in the ledgered event."""
    with _serve(tmp_path, actor="page-actor") as base:
        body = """
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  // create a draft from the template
  await page.fill('input.field', 'exp-ui');
  await page.evaluate(() => { [...document.querySelectorAll('button')].find(b => b.textContent === 'New draft').click(); });
  await page.waitForTimeout(1200);
  // drop hypothesized_effect so the quick-lock power gate is deterministic
  await page.evaluate(() => {
    const pane = document.querySelector('textarea.pane');
    pane.value = pane.value.split('\\n').filter(l => !l.startsWith('hypothesized_effect')).join('\\n');
    pane.dispatchEvent(new Event('input'));
  });
  await page.evaluate(() => { [...document.querySelectorAll('button')].find(b => b.textContent === 'Save draft').click(); });
  await page.waitForTimeout(1500);
  out.state = await page.evaluate(() => window.__vb());
  // the ceremony: attest and lock
  await page.fill('input[placeholder*="attested_by"]', 'jyang');
  await page.evaluate(() => { [...document.querySelectorAll('button')].find(b => b.textContent === 'Lock pre-registration').click(); });
  await page.waitForTimeout(2500);
  out.after = await page.evaluate(() => window.__vb());
  out.lockedChip = await page.evaluate(() => document.body.textContent.includes('locked (immutable)'));
"""
        out = drive(base, body, tmp_path)
        assert out["state"]["name"] == "exp-ui" and out["state"]["dirty"] == 0
        assert out["after"]["locked"] is True and out["lockedChip"] is True
        assert out["__errors"] == []
        evs = read_events(tmp_path / "exp-ui" / "ledger.ndjson")
        assert [e["event"] for e in evs] == ["experiment_locked"]
        assert evs[0]["provenance"]["actor"] == "page-actor"
        assert evs[0]["attestation"]["attested_by"] == "jyang"


# --- AC-3: post-lock immutability -----------------------------------------------------
def test_ac3_post_lock_readonly_refusals(tmp_path):
    with _serve(tmp_path) as base:
        _save_draft(base)
        _post(base, "/api/lock", {"name": "exp-a", "attested_by": "jyang"})

        code, err = _post(base, "/api/draft", {"name": "exp-a",
                                               "files": {"experiment.yaml": "tampered: true\n"}})
        assert code == 409 and "immutable" in err["error"]
        # the pre-registered bytes did not move
        assert (tmp_path / "exp-a" / "experiment.yaml").read_text(encoding="utf-8") == SPEC

        doc = _get(base, "/api/draft?name=exp-a")
        assert doc["locked"] is True and doc["lock"]["attested_by"] == "jyang"

        # a fresh draft in a fresh directory proceeds — re-planning, not amending
        code, _ = _post(base, "/api/draft", {"name": "exp-a-v2",
                                             "files": {"experiment.yaml": SPEC}})
        assert code == 200


# --- AC-4: posture ------------------------------------------------------------------
def test_ac4_posture_actor_needles_routes(tmp_path, monkeypatch):
    from harness.author.page import AUTHOR_PAGE

    # the page is self-contained (the needle property, script allowed)
    for needle in ("http://", "https://", "src=", "href=", "url(", "@import", "<link"):
        assert needle not in AUTHOR_PAGE, f"external/active reference {needle!r}"
    assert "Authoring surface" in AUTHOR_PAGE  # the standing banner

    # actor is launch-bound and refused loudly, never defaulted
    import getpass

    from harness.cli import app

    monkeypatch.setattr(getpass, "getuser", lambda: (_ for _ in ()).throw(OSError("no user")))
    result = CliRunner().invoke(app, ["author", str(tmp_path)])
    assert result.exit_code == 2 and "--actor" in result.output

    assert DEFAULT_HOST == "127.0.0.1"  # loopback by default: mutating surface

    with _serve(tmp_path) as base:
        _save_draft(base)
        before = _digest(tmp_path)
        for path in ("/api/drafts", "/api/draft?name=exp-a", "/api/validate?name=exp-a",
                     "/api/sha?name=exp-a"):
            _get(base, path)
        assert _digest(tmp_path) == before  # GETs are side-effect-free

        code, _ = _post(base, "/api/validate", {"name": "exp-a"})
        assert code == 404  # only the two ceremony endpoints accept POST
        req = urllib.request.Request(base + "/api/lock", data=b"", method="DELETE")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req)
        assert excinfo.value.code == 405
        assert excinfo.value.headers["Allow"] == "GET, POST"
        code, _ = _post(base, "/api/draft", {"name": "exp-a",
                                             "files": {"../escape.yaml": "x"}})
        assert code == 409  # draft files are allowlisted, never path-joined


def test_h2_csrf_guard_refuses_cross_site_lock(tmp_path):
    """PRA-H2: a cross-site page must not be able to forge the lock ceremony.
    A POST with a foreign/absent Origin, a foreign Host, or a text/plain body is
    refused (403) and appends no experiment_locked event."""
    with _serve(tmp_path) as base:
        _save_draft(base)
        # foreign Origin (the cross-site attacker's page)
        code, _ = _post(base, "/api/lock", {"name": "exp-a", "attested_by": "x"},
                        headers={"Origin": "http://evil.example"})
        assert code == 403
        # missing Origin entirely
        code, _ = _post(base, "/api/lock", {"name": "exp-a", "attested_by": "x"},
                        headers={"Origin": None})
        assert code == 403
        # text/plain no-cors bypass
        code, _ = _post(base, "/api/lock", {"name": "exp-a", "attested_by": "x"},
                        headers={"Content-Type": "text/plain"})
        assert code == 403
        # foreign Host (DNS-rebinding)
        code, _ = _post(base, "/api/lock", {"name": "exp-a", "attested_by": "x"},
                        headers={"Host": "evil.example"})
        assert code == 403
        # nothing was locked by any of the refused requests
        assert find_events(tmp_path / "exp-a" / "ledger.ndjson", "experiment_locked") == []
        # the legitimate same-origin ceremony still works
        code, locked = _post(base, "/api/lock", {"name": "exp-a", "attested_by": "jyang"})
        assert code == 200 and locked["locked"] is True


# --- AC-5: commitment parity with bench plan --------------------------------------------
def test_ac5_tasks_rubric_commitment_parity(tmp_path):
    """The ceremony and `bench plan` lock the same bytes to payload-identical
    genesis events (modulo provenance and the differing absolute spec_path)."""
    from harness.cli import app

    for name in ("via-ceremony", "via-cli"):
        d = tmp_path / name
        (d / "rubrics").mkdir(parents=True)
        (d / "experiment.yaml").write_text(SPEC, encoding="utf-8")
        (d / "tasks.yaml").write_text(TASKS, encoding="utf-8")
        (d / "rubrics" / "r.md").write_text(RUBRIC, encoding="utf-8")

    with _serve(tmp_path, actor="parity", lock_kwargs=None) as base:  # full fidelity
        code, _ = _post(base, "/api/lock", {"name": "via-ceremony", "attested_by": "jyang"})
        assert code == 200

    cli = tmp_path / "via-cli"
    result = CliRunner().invoke(app, [
        "plan", str(cli / "experiment.yaml"), "--ledger", str(cli / "ledger.ndjson"),
        "--attested-by", "jyang", "--actor", "parity",
    ])
    assert result.exit_code == 0, result.output

    def payload(dirname: str) -> dict:
        ev = dict(read_events(tmp_path / dirname / "ledger.ndjson")[0])
        for volatile in ("provenance", "prev_hash", "spec_path"):
            ev.pop(volatile, None)
        return ev

    ceremony, cli_lock = payload("via-ceremony"), payload("via-cli")
    assert ceremony == cli_lock  # same bytes, same commitment, same event
    # and the paths differ only by route taken, not by content committed
    assert ceremony["task_commitment"] == cli_lock["task_commitment"]
    assert ceremony["rubric_sha256"] == cli_lock["rubric_sha256"]
