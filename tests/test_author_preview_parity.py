"""Author preview ⇄ lock parity [refactor 02 §4].

The audited gap: the authoring ``/api/validate`` preview re-implemented *some*
of ``lock_experiment``'s checks but not the platform-capability one, so a draft
naming an arm platform with no registered adapter previewed green and then
refused at lock with ``UnknownArmPlatformError``. The fix composes the SAME
``check_arm_platforms`` preflight step in the preview, so parity is by
construction.

These drive the real author server over loopback HTTP (no browser) — the
platform-parity behavior is server-side, so it is exercisable here even though
the page-driving acceptance tests in ``test_eval17_author.py`` are browser-gated.
Preview purity (no ledger, no lock taken) is asserted alongside.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from harness.author.server import make_author_server
from harness.plan.lock import UnknownArmPlatformError, lock_experiment
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.servers import running_server

# Two registered platforms (claude_code, codex) — the lock accepts these.
_GOOD_SPEC = """arms:
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
# treatment names an unregistered platform: valid *shape*, unrunnable *here*.
_BAD_SPEC = _GOOD_SPEC.replace("platform: codex", "platform: my_custom_stack")
_TASKS = "tasks:\n  - id: t1\n    prompt: p\n"
_RUBRIC = "judge on correctness\n"

_QUICK = {"n_sim": 8, "n_boot": 40, "deltas": [0.2, 0.4]}


@contextmanager
def _serve(root: Path):
    srv = make_author_server(root, actor="tester", port=0, lock_kwargs=_QUICK)
    with running_server(srv) as base:
        yield base


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path) as resp:
        return json.loads(resp.read())


def _save(base: str, name: str, spec: str) -> None:
    body = json.dumps(
        {"name": name, "files": {"experiment.yaml": spec, "tasks.yaml": _TASKS,
                                 "rubrics/r.md": _RUBRIC}}
    ).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/draft", data=body, method="POST",
        headers={"Content-Type": "application/json", "Origin": base},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200


def _write_dir(root: Path, name: str, spec: str) -> Path:
    d = root / name
    (d / "rubrics").mkdir(parents=True)
    (d / "experiment.yaml").write_text(spec, encoding="utf-8")
    (d / "tasks.yaml").write_text(_TASKS, encoding="utf-8")
    (d / "rubrics" / "r.md").write_text(_RUBRIC, encoding="utf-8")
    return d


def test_validate_surfaces_unknown_platform_that_lock_would_refuse(tmp_path):
    """Parity: a preview that is otherwise green flags the unregistered platform,
    naming the offending arm — the same refusal the lock raises."""
    with _serve(tmp_path) as base:
        _save(base, "bad", _BAD_SPEC)
        v = _get(base, "/api/validate?name=bad")
        assert v["spec"]["ok"] is True  # shape is valid; the gap was here
        assert v["platform"]["ok"] is False
        assert v["platform"]["error_class"] == "UnknownArmPlatformError"
        assert "treatment" in v["platform"]["error"]
        assert "my_custom_stack" in v["platform"]["error"]

    # the lock refuses the very same draft for the very same reason (parity).
    d = _write_dir(tmp_path, "bad-cli", _BAD_SPEC)
    try:
        lock_experiment(d / "experiment.yaml", d / "ledger.ndjson",
                        ctx=fixed_ctx(), task_dicts=None, **_QUICK)
        raise AssertionError("lock should have refused the unknown platform")
    except UnknownArmPlatformError as e:
        assert "my_custom_stack" in str(e)


def test_validate_passes_platform_for_registered_arms(tmp_path):
    with _serve(tmp_path) as base:
        _save(base, "good", _GOOD_SPEC)
        v = _get(base, "/api/validate?name=good")
        assert v["spec"]["ok"] is True
        assert v["platform"] == {"ok": True}
        # unchanged sub-objects: the spec/tasks payloads keep today's shape
        assert v["tasks"] == {"ok": True, "count": 1, "ids": ["t1"]}


def test_validate_is_a_pure_read_even_with_platform_check(tmp_path):
    """The added platform step must not write, lock, or otherwise mutate — the
    preview stays a pure read over the saved draft bytes."""
    with _serve(tmp_path) as base:
        _save(base, "good", _GOOD_SPEC)
        draft = tmp_path / "good"
        digest_before = sorted(
            (str(p.relative_to(tmp_path)), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in tmp_path.rglob("*") if p.is_file()
        )
        for _ in range(3):
            _get(base, "/api/validate?name=good")
        digest_after = sorted(
            (str(p.relative_to(tmp_path)), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in tmp_path.rglob("*") if p.is_file()
        )
        assert digest_after == digest_before
        assert not (draft / "ledger.ndjson").exists()


def test_validate_omits_platform_when_spec_unparseable(tmp_path):
    """A parse failure has no spec to check platforms on — the platform key is
    simply absent (never a crash), and the spec error is still surfaced."""
    with _serve(tmp_path) as base:
        # drop cost_ceiling → MissingCostCeilingError at parse
        _save(base, "noceil", _GOOD_SPEC.replace(
            "cost_ceiling: {amount: 5.0, currency: USD}\n", ""))
        v = _get(base, "/api/validate?name=noceil")
        assert v["spec"]["ok"] is False
        assert "platform" not in v
