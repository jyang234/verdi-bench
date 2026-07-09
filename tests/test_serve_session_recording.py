"""Captured claude-session transcripts on the operator surface [flight-recorder
charter: max observability for claude_code arms].

A ``platform: claude_code`` trial yields no verdi-format trajectory/reasoning
(the adapter honestly returns ``None``), but the CLI's full session transcript
is captured verbatim under ``<artifacts>/claude-session/**/*.jsonl``. These
tests pin the serve-tier normalizer that turns that JSONL into an ordered feed,
and the ``/api/trial`` / compare / bundle payloads that surface it — built from
the trial's LEDGER ``artifacts_path`` (never client input), absent-safe, and
capped so a runaway transcript cannot swamp the page.

Normalizer fixtures are hand-built here, never copied from ``runs/``.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from harness.ledger.query import read_events
from harness.serve.bundle import collect_bundle_data
from harness.serve.compare import paired_comparisons
from harness.serve.session_recording import (
    load_session_recording,
    normalize_session,
)
from harness.judge.assemble import comparison_id_for
from harness.status.trial import trial_detail
from tests.fixtures.browser import drive
from tests.fixtures.scenarios import rich_experiment
from tests.fixtures.servers import serve_experiment, serve_root


def _jsonl(*objs: dict) -> str:
    return "\n".join(json.dumps(o) for o in objs) + "\n"


# A kitchen-sink transcript exercising every rendered block type plus events
# that must be skipped without crashing. Content carries markup so the escaping
# contract (rendered via textContent) has something to neutralize.
HAPPY = _jsonl(
    {"type": "user", "message": {"role": "user", "content": "solve <the> task"}},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "let me plan first", "signature": "sig"},
        {"type": "text", "text": "I'll start by <reading> files"},
        {"type": "tool_use", "id": "tu1", "name": "Bash",
         "input": {"command": "ls -la", "description": "list"}},
    ]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu1",
         "content": "main.go\ncore.go", "is_error": False},
    ]}},
    {"type": "ai-title", "title": "irrelevant"},
    {"type": "queue-operation", "op": "flush"},
    {"type": "attachment", "path": "x"},
)


# --- the pure normalizer -----------------------------------------------------------
def test_normalize_happy_path_kinds_roles_names():
    out = normalize_session([("s.jsonl", HAPPY)])
    assert out["skipped_lines"] == 0
    assert out["more_entries"] == 0
    assert [f["label"] for f in out["files"]] == ["s.jsonl"]
    entries = out["files"][0]["entries"]
    assert [e["kind"] for e in entries] == [
        "message", "message", "message", "tool_use", "tool_result"
    ]
    # roles follow the message envelope: assistant blocks are assistant, the
    # tool_result comes back inside a user turn
    assert [e["role"] for e in entries] == [
        "user", "assistant", "assistant", "assistant", "user"
    ]
    assert entries[0]["detail"] == "solve <the> task"          # string content verbatim
    assert entries[1]["detail"] == "let me plan first"         # thinking → message body
    assert entries[3]["name"] == "Bash"                        # tool name surfaced
    assert "ls -la" in entries[3]["detail"]                    # tool input rendered
    assert "main.go" in entries[4]["detail"]                   # tool result rendered
    # entry count is surfaced and matches
    assert out["entry_count"] == 5
    # only tool_use carries a name
    assert "name" not in entries[0] and "name" not in entries[4]


def test_normalize_thinking_block_becomes_a_message_entry():
    # JUDGMENT CALL: the spec enumerated text/tool_use/tool_result blocks; a
    # `thinking` block is the agent's reasoning and the charter mandates max
    # observability, so it is surfaced as a `message` entry (assistant role)
    # rather than dropped. Kept inside the spec's 3-kind vocabulary.
    out = normalize_session([("s.jsonl", _jsonl(
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "deep thought", "signature": "s"}]}}))])
    e = out["files"][0]["entries"][0]
    assert e == {"kind": "message", "role": "assistant", "detail": "deep thought"}


def test_normalize_detail_cap_marks_elided_count():
    big = "x" * 2500
    out = normalize_session([("s.jsonl", _jsonl(
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": big}]}}))],
        detail_cap=2000)
    detail = out["files"][0]["entries"][0]["detail"]
    assert detail.startswith("x" * 2000)
    assert "500 chars elided" in detail            # 2500 - 2000
    # a body at the cap is not marked
    exact = normalize_session([("s.jsonl", _jsonl(
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "y" * 2000}]}}))],
        detail_cap=2000)
    assert exact["files"][0]["entries"][0]["detail"] == "y" * 2000
    assert "elided" not in exact["files"][0]["entries"][0]["detail"]


def test_normalize_entry_cap_marks_more_entries():
    blocks = [{"type": "text", "text": f"m{i}"} for i in range(600)]
    out = normalize_session([("s.jsonl", _jsonl(
        {"type": "assistant", "message": {"role": "assistant", "content": blocks}}))],
        entry_cap=500)
    assert out["entry_count"] == 500
    assert out["more_entries"] == 100
    assert sum(len(f["entries"]) for f in out["files"]) == 500


def test_normalize_unparseable_lines_counted_blanks_ignored():
    text = (
        "this is not json\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
        + "\n\n   \n"          # blank + whitespace-only lines are not "unparseable"
        + "{ still not json\n"
    )
    out = normalize_session([("s.jsonl", text)])
    assert out["skipped_lines"] == 2
    assert out["files"][0]["skipped_lines"] == 2
    assert out["entry_count"] == 1                 # the one good line still parsed


def test_normalize_skips_non_message_and_unknown_blocks_without_crashing():
    out = normalize_session([("s.jsonl", _jsonl(
        {"type": "ai-title", "title": "t"},
        {"type": "attachment", "path": "p"},
        {"type": "summary", "summary": "s"},                 # non user/assistant event
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "image", "source": {"data": "..."}},    # unknown block kind
            {"type": "text", "text": "the only rendered block"}]}},
        {"type": "user", "message": {"role": "user", "content": 42}},  # bad content type
    ))])
    # only the single text block yields an entry; nothing raised, nothing counted
    # as an unparseable LINE (those events parsed fine, they are just not feed rows)
    assert out["entry_count"] == 1
    assert out["skipped_lines"] == 0
    assert out["files"][0]["entries"][0]["detail"] == "the only rendered block"


def test_normalize_tool_result_list_content_is_flattened():
    out = normalize_session([("s.jsonl", _jsonl(
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": "line one"},
                {"type": "text", "text": "line two"}]}]}}))])
    e = out["files"][0]["entries"][0]
    assert e["kind"] == "tool_result"
    assert "line one" in e["detail"] and "line two" in e["detail"]


def test_normalize_multi_file_concatenates_in_given_order_labeled():
    a = _jsonl({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": "from A"}]}})
    b = _jsonl({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": "from B"}]}})
    out = normalize_session([("a.jsonl", a), ("sub/b.jsonl", b)])
    assert [f["label"] for f in out["files"]] == ["a.jsonl", "sub/b.jsonl"]
    assert out["files"][0]["entries"][0]["detail"] == "from A"
    assert out["files"][1]["entries"][0]["detail"] == "from B"
    assert out["entry_count"] == 2


def test_normalize_entry_cap_spans_files():
    # the total cap is global across the concatenated files, not per file
    a = _jsonl({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": f"a{i}"} for i in range(3)]}})
    b = _jsonl({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": f"b{i}"} for i in range(3)]}})
    out = normalize_session([("a.jsonl", a), ("b.jsonl", b)], entry_cap=4)
    assert out["entry_count"] == 4
    assert out["more_entries"] == 2
    assert len(out["files"][0]["entries"]) == 3    # first file fits
    assert len(out["files"][1]["entries"]) == 1    # second file truncated by the global cap


# --- the impure loader (reads files) -----------------------------------------------
def test_load_returns_none_without_a_session_dir(tmp_path):
    assert load_session_recording(tmp_path) is None
    assert load_session_recording(None) is None
    assert load_session_recording("") is None


def test_load_returns_none_when_session_dir_has_no_jsonl(tmp_path):
    (tmp_path / "claude-session").mkdir()
    (tmp_path / "claude-session" / "notes.txt").write_text("hi", encoding="utf-8")
    assert load_session_recording(tmp_path) is None


def test_load_reads_sorted_paths_with_relative_posix_labels(tmp_path):
    base = tmp_path / "claude-session" / "-workspace"
    base.mkdir(parents=True)
    (base / "b.jsonl").write_text(_jsonl(
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "B"}]}}), encoding="utf-8")
    (base / "a.jsonl").write_text(_jsonl(
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "A"}]}}), encoding="utf-8")
    sub = base / "session" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent.jsonl").write_text(_jsonl(
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "SUB"}]}}), encoding="utf-8")
    out = load_session_recording(tmp_path)
    labels = [f["label"] for f in out["files"]]
    assert labels == sorted(labels)                 # sorted path order
    assert labels[0] == "-workspace/a.jsonl"        # relative to claude-session/, posix
    assert "-workspace/session/subagents/agent.jsonl" in labels


# --- /api/trial route --------------------------------------------------------------
def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _artifacts_path(ledger: Path, trial_id: str) -> Path:
    for ev in read_events(ledger):
        if ev.get("event") == "trial" and ev["trial_record"]["trial_id"] == trial_id:
            return Path(ev["trial_record"]["artifacts_path"])
    raise KeyError(trial_id)


def _plant(artifacts: Path, rel: str, text: str) -> None:
    dest = artifacts / "claude-session" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def test_api_trial_surfaces_session_recording_when_present(tmp_path):
    fx = rich_experiment(tmp_path)
    tid = fx["trial_ids"][("t1", "treatment")]
    _plant(_artifacts_path(fx["ledger"], tid), "-workspace/s.jsonl", HAPPY)
    with serve_experiment(tmp_path) as base:
        d = _get_json(base + f"/api/trial?id={tid}")
    assert "session_recording" in d
    sr = d["session_recording"]
    assert sr["files"][0]["label"] == "-workspace/s.jsonl"
    assert [e["kind"] for e in sr["files"][0]["entries"]] == [
        "message", "message", "message", "tool_use", "tool_result"]


def test_api_trial_payload_is_unchanged_when_transcript_absent(tmp_path):
    # the pin: a trial with no claude-session artifacts serves EXACTLY the
    # trial_detail payload it served before this feature — no new key.
    fx = rich_experiment(tmp_path)
    tid = fx["trial_ids"][("t2", "control")]
    with serve_experiment(tmp_path) as base:
        d = _get_json(base + f"/api/trial?id={tid}")
    assert "session_recording" not in d
    assert d == json.loads(json.dumps(trial_detail(tmp_path, tid)))


def test_api_trial_reads_only_the_trials_ledger_artifacts_path(tmp_path):
    # security posture [PRA-M10]: the path is ledger-derived, not client input.
    # A transcript planted elsewhere in the tree is never this trial's recording.
    fx = rich_experiment(tmp_path)
    tid = fx["trial_ids"][("t2", "control")]
    stray = tmp_path / "claude-session" / "s.jsonl"
    stray.parent.mkdir(parents=True)
    stray.write_text(HAPPY, encoding="utf-8")
    with serve_experiment(tmp_path) as base:
        d = _get_json(base + f"/api/trial?id={tid}")
    assert "session_recording" not in d


# --- compare payload ---------------------------------------------------------------
def test_compare_surfaces_session_recording_per_arm(tmp_path):
    fx = rich_experiment(tmp_path)
    arm_a = paired_comparisons(tmp_path)["arm_a"]      # positional A per lock order
    tid_a = fx["trial_ids"][("t1", arm_a)]
    _plant(_artifacts_path(fx["ledger"], tid_a), "s.jsonl", HAPPY)
    c = paired_comparisons(tmp_path)
    pair = next(p for p in c["pairs"] if p["task_id"] == "t1")
    assert "session_recording" in pair["a"]
    assert pair["a"]["session_recording"]["files"][0]["label"] == "s.jsonl"
    assert "session_recording" not in pair["b"]         # arm B had no transcript


def test_compare_omits_session_recording_when_absent(tmp_path):
    rich_experiment(tmp_path)
    c = paired_comparisons(tmp_path)
    for p in c["pairs"]:
        assert "session_recording" not in p["a"]
        assert "session_recording" not in p["b"]


# --- static bundle (route parity for the offline snapshot) -------------------------
def test_bundle_trials_carry_session_recording(tmp_path):
    fx = rich_experiment(tmp_path)
    tid = fx["trial_ids"][("t1", "treatment")]
    _plant(_artifacts_path(fx["ledger"], tid), "-workspace/s.jsonl", HAPPY)
    data = collect_bundle_data(tmp_path)
    assert "session_recording" in data["trials"][tid]
    # a trial with no transcript keeps the pre-feature payload (no key)
    other = fx["trial_ids"][("t2", "control")]
    assert "session_recording" not in data["trials"][other]


# --- page rendering (browser-marked; skips honestly without the stack) -------------
# A transcript that tries to break out of textContent — the render must neutralize
# every one of these (all content lands via textContent, never innerHTML).
INJECT = _jsonl(
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "<img src=x onerror=\"window.__pwned=1\">"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "echo <hi>"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result",
         "content": "out</pre><script>window.__pwned=1</script>"}]}},
)


@pytest.mark.browser
def test_process_tab_renders_and_escapes_session_recording(tmp_path):
    fx = rich_experiment(tmp_path / "exp-s")
    tid = fx["trial_ids"][("t1", "treatment")]
    _plant(_artifacts_path(fx["ledger"], tid), "-workspace/s.jsonl", INJECT)
    with serve_root(tmp_path) as base:
        body = """
  await page.goto(BASE + '/#/exp/exp-s/trial/""" + tid + """?tab=process', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  out.p = await page.evaluate(() => {
    const app = document.getElementById('app');
    return {
      label: app.textContent.includes('session flight recording (captured transcript)'),
      file: app.textContent.includes('-workspace/s.jsonl'),
      entries: app.querySelectorAll('.sessentry').length,
      toolName: app.textContent.includes('tool · Bash'),
      // escaped: the literal injection text is present as DATA...
      literalText: app.textContent.includes('<img src=x onerror='),
      // ...and none of it became live DOM / ran
      injectedImg: document.querySelectorAll('img').length,
      pwned: !!window.__pwned,
      actionOnly: app.textContent.includes('no process to show') };
  });
"""
        out = drive(base, body, tmp_path)
        assert out["p"]["label"] is True
        assert out["p"]["file"] is True
        assert out["p"]["entries"] == 3            # text, tool_use, tool_result
        assert out["p"]["toolName"] is True
        assert out["p"]["literalText"] is True     # rendered as escaped text
        assert out["p"]["injectedImg"] == 0        # no element created
        assert out["p"]["pwned"] is False          # no script/onerror ran
        assert out["p"]["actionOnly"] is False     # the transcript IS the process
        assert out["__errors"] == []


@pytest.mark.browser
def test_compare_drawer_renders_session_recording(tmp_path):
    fx = rich_experiment(tmp_path / "exp-s")
    # plant on both t1 arms so the drawer renders regardless of lock order
    for arm in ("control", "treatment"):
        _plant(_artifacts_path(fx["ledger"], fx["trial_ids"][("t1", arm)]),
               "-workspace/s.jsonl", INJECT)
    cmp_id = comparison_id_for("t1", 0)
    with serve_root(tmp_path) as base:
        body = """
  await page.goto(BASE + '/#/exp/exp-s/compare?sr=""" + cmp_id + """', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2200);
  out.c = await page.evaluate(() => {
    const app = document.getElementById('app');
    const sr = app.querySelector('details.sr');
    return {
      present: !!sr,
      open: sr ? sr.hasAttribute('open') : false,
      summary: sr ? sr.querySelector('summary').textContent : '',
      cols: app.querySelectorAll('details.sr .rz > div').length,
      entries: app.querySelectorAll('details.sr .sessentry').length,
      pwned: !!window.__pwned };
  });
"""
        out = drive(base, body, tmp_path)
        assert out["c"]["present"] is True
        assert out["c"]["open"] is True                       # ?sr= deep-link reproduces it
        assert "session flight recording (captured transcript)" in out["c"]["summary"]
        assert out["c"]["cols"] == 2                          # both arm columns
        assert out["c"]["entries"] == 6                       # 3 per arm
        assert out["c"]["pwned"] is False
        assert out["__errors"] == []
