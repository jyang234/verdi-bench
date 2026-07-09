#!/usr/bin/env python3
"""verdi_agent — the stdlib-only in-image SDK for a verdi trial image [refactor 03 §2].

A single file, shipped read-only into every ``verdi-base`` build and importable
by any trial agent (``import verdi_agent``). It bakes in the parts of the harbor
compatibility contract (``docs/images.md`` §1) that stdlib will not do for you,
so writing a trial image means writing *agent logic only*:

* :func:`read_request` — typed accessors over the read-only ``/verdi/request.json``
  mount (prompt / arm / model / payload; tolerant of the pre-A1 shape that has no
  ``schema_version``).
* :func:`post_json` — one correct implementation of the CONNECT-tunnel +
  ``Proxy-Authorization`` dance the metering proxy requires (the per-trial
  credential rides ``HTTP(S)_PROXY`` userinfo; stdlib ``urllib`` will not add it
  on a CONNECT). This used to be ~35 lines hand-copied into every reference agent.
* :class:`AgentLog` — a writer for the FROZEN verdi generic log format v1/v2
  (``docs/adapters.md``): ``message`` / ``tool_call`` / ``file_edit`` / ``test_run``
  trajectory steps, ``reasoning`` flight-recorder entries, and
  :meth:`AgentLog.finish` which emits ``artifacts/agent_log.json``. It is a
  writer for that contract, never a new format.
* :func:`run_visible` — the fail-visible wrapper: any error still leaves a
  scorable ``agent_log.json`` and exits nonzero (a nonzero agent exit is still a
  *completed*, gradeable trial for verdi; the runner reserves 124/125).
* :func:`capture_claude_session_transcripts` — flight-recorder capture of the
  claude CLI's on-disk session store (``$HOME/.claude/projects/**/*.jsonl``) into
  ``artifacts/claude-session/``, for the ``claude_code``-platform images.

**Hard constraint: standard library only.** The base image installs no pip
packages and has no network at build time, so this module must import nothing
outside the stdlib. It is version-stamped (:data:`VERDI_AGENT_VERSION`) so an
image's SDK vintage is legible.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import pathlib
import shutil
import sys
import urllib.parse
from typing import Any, Callable, Optional

# Bump on any behavior change to this file; the image bakes a known vintage in.
VERDI_AGENT_VERSION = "verdi-agent-1.2.0"

# The fixed workspace layout of the harbor contract [docs/images.md §1]:
#   /workspace              — the graded workspace (bind-mounted rw by harbor)
#   /workspace/artifacts/   — telemetry lands here
#   /verdi/request.json     — prompt + arm config, read-only, OUTSIDE /workspace
WORKSPACE = pathlib.Path("/workspace")
ARTIFACTS = WORKSPACE / "artifacts"
REQUEST_PATH = pathlib.Path("/verdi/request.json")
AGENT_LOG_PATH = ARTIFACTS / "agent_log.json"

# The generic log format's version key and the versions this writer emits.
VERSION_KEY = "verdi_log_version"

# The CLOSED sub-agent role vocabulary [EVAL-21 AC-3] — a MIRROR of
# ``harness.run.trajectory.AGENT_ROLES``, kept byte-identical by the images
# compatibility contract (a parity test in the harness suite fails if they
# drift). A label is ``role`` optionally with a small ordinal (``worker-2``).
# Identity leakage (a model/vendor/arm name) is unrepresentable, not scrubbed:
# an out-of-vocabulary label is refused here, in-image, rather than failing the
# trial closed later at the harness parser.
AGENT_ROLES = frozenset(
    {
        "planner",
        "executor",
        "orchestrator",
        "router",
        "critic",
        "reviewer",
        "tester",
        "researcher",
        "worker",
    }
)

# Telemetry field names of the ``telemetry`` block [docs/adapters.md]; the writer
# never emits a key outside this set (a declared log refuses unknown keys).
_TELEMETRY_FIELDS = (
    "tokens_in",
    "tokens_out",
    "tokens_cache",
    "cost",
    "wall_time_s",
    "tool_calls",
)


class RequestError(RuntimeError):
    """``/verdi/request.json`` was absent or not a JSON object [refactor 03 §1].

    Raised loudly rather than papered over: an image that cannot read its task is
    misconfigured, and :func:`run_visible` turns the raise into a scorable
    fail-visible log."""


class Request:
    """Typed, read-only view of ``/verdi/request.json`` [refactor 03 §1, A1].

    The harness writes ``{schema_version, prompt, arm, model, payload}`` (A1); a
    pre-A1 engine wrote the same keys WITHOUT ``schema_version``. Accessors
    tolerate the absence (``schema_version`` is ``None`` then), so one agent runs
    against either engine — the A1 migration story is "additive field".
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    @property
    def prompt(self) -> str:
        """The agent-visible task text."""
        return self._data.get("prompt", "")

    @property
    def arm(self) -> str:
        """Which arm this container is (identity-blind: a name, never a vendor)."""
        return self._data.get("arm", "")

    @property
    def model(self) -> str:
        """The arm's model id, ``provider/model`` (date-versioned, never an alias)."""
        return self._data.get("model", "")

    @property
    def payload(self) -> dict:
        """The arm's free-form config block (temperature, stack version, …)."""
        p = self._data.get("payload")
        return p if isinstance(p, dict) else {}

    @property
    def schema_version(self) -> Optional[int]:
        """The request-file schema version (A1); ``None`` under a pre-A1 engine."""
        v = self._data.get("schema_version")
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    @property
    def provider(self) -> str:
        """The model id's provider segment (``anthropic`` from ``anthropic/…``)."""
        return self.model.split("/", 1)[0] if "/" in self.model else self.model

    @property
    def model_id(self) -> str:
        """The model id without its provider prefix (``claude-…`` from ``anthropic/claude-…``)."""
        return self.model.split("/", 1)[-1]

    def get(self, key: str, default: Any = None) -> Any:
        """Escape hatch for a raw key (e.g. a future additive field)."""
        return self._data.get(key, default)


def read_request(path: pathlib.Path = REQUEST_PATH) -> Request:
    """Load and type the trial request mounted at ``/verdi/request.json``.

    Raises :class:`RequestError` if the file is missing or not a JSON object —
    a misconfigured mount must fail loudly, not silently yield an empty prompt.
    """
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise RequestError(f"trial request {path} is unreadable: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RequestError(f"trial request {path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise RequestError(
            f"trial request {path} must be a JSON object, got {type(data).__name__}"
        )
    return Request(data)


# Where the pinned claude CLI keeps its per-project session transcripts, and the
# artifacts subdir a trial preserves them under.
CLAUDE_PROJECTS_SUBPATH = pathlib.Path(".claude") / "projects"
SESSION_CAPTURE_DIRNAME = "claude-session"


def capture_claude_session_transcripts() -> None:
    """Copy the claude CLI's session transcripts into ``artifacts/claude-session/``.

    The pinned ``claude`` CLI (the ``claude_code``-platform images) writes its full
    session transcript — every message and tool call — as JSONL under
    ``$HOME/.claude/projects/<slug>/<session-id>.jsonl``; uncaptured, that evidence
    dies with the trial container. Each file is copied to
    ``artifacts/claude-session/<path relative to projects/>`` so distinct project
    slugs cannot collide. Two load-bearing constraints:

    * ``artifacts/`` is excluded from the judged diff (the groundwork-mcp.jsonl
      precedent), so a captured transcript can never surface as a judged
      treatment-arm asymmetry;
    * the capture is UNCONDITIONAL and symmetric across arms — every arm carries
      the identical evidence surface.

    Supplementary flight-recorder evidence only: the native ``agent_log.json``
    stays authoritative. No transcripts → nothing written, no directory created
    (the absence is visible downstream, not a failure). An unreadable file is a
    one-line stderr warning naming it — never a trial failure.
    """
    home = os.environ.get("HOME") or "/tmp"
    projects = pathlib.Path(home) / CLAUDE_PROJECTS_SUBPATH
    if not projects.is_dir():
        return
    for src in sorted(projects.rglob("*.jsonl")):
        dst = ARTIFACTS / SESSION_CAPTURE_DIRNAME / src.relative_to(projects)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError as e:
            print(
                f"verdi-agent: warning: could not capture session transcript {src}: {e}",
                file=sys.stderr,
            )


def post_json(
    host: str,
    path: str,
    headers: dict,
    body: dict,
    *,
    timeout: float = 180.0,
) -> dict:
    """POST ``body`` as JSON to ``https://{host}{path}`` and return the parsed reply.

    Egress from a trial is default-deny: the only route out is the metering proxy
    addressed by ``HTTP(S)_PROXY``. When that variable is set this CONNECT-tunnels
    through it and, if the proxy URL carries userinfo (harbor injects the trial id
    as the username so the proxy attributes egress per-trial), sends it as a
    ``Proxy-Authorization: Basic`` header on the CONNECT — the one thing stdlib
    ``urllib`` will not do for you. With no proxy set it connects directly (the
    ``verify`` / offline path). A ``>= 400`` response raises loudly [refactor 03 §2].
    """
    data = json.dumps(body).encode("utf-8")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        pu = urllib.parse.urlparse(proxy)
        conn = http.client.HTTPSConnection(pu.hostname, pu.port or 3128, timeout=timeout)
        tunnel_headers = {}
        if pu.username is not None:
            cred = base64.b64encode(
                f"{pu.username}:{pu.password or ''}".encode()
            ).decode()
            tunnel_headers["Proxy-Authorization"] = "Basic " + cred
        conn.set_tunnel(host, 443, headers=tunnel_headers)
    else:
        conn = http.client.HTTPSConnection(host, 443, timeout=timeout)
    conn.request(
        "POST", path, body=data, headers={**headers, "content-type": "application/json"}
    )
    resp = conn.getresponse()
    raw = resp.read()
    if resp.status >= 400:
        raise RuntimeError(f"HTTP {resp.status} from {host}{path}: {raw[:200]!r}")
    return json.loads(raw)


def _validate_agent(label: Optional[str]) -> Optional[str]:
    """Closed-vocabulary role check (``None`` or ``role[-ordinal]``) [EVAL-21 AC-3]."""
    if label is None:
        return None
    role, _, ordinal = label.partition("-")
    ok = role in AGENT_ROLES and (
        ordinal == "" or (ordinal.isdigit() and 1 <= len(ordinal) <= 3)
    )
    if not ok:
        raise ValueError(
            f"agent label {label!r} is not in the closed role vocabulary "
            f"{sorted(AGENT_ROLES)} (optionally '-<ordinal>', e.g. 'worker-2')"
        )
    return label


class AgentLog:
    """An incremental writer for the verdi generic log format v1/v2 [refactor 03 §2].

    Accumulate trajectory steps (:meth:`message`, :meth:`tool_call`,
    :meth:`file_edit`, :meth:`test_run`) and flight-recorder reasoning
    (:meth:`reasoning`), then call :meth:`finish` to emit
    ``artifacts/agent_log.json``. The emitted version is auto-selected: **v2** when
    any per-model telemetry is reported or any step/entry is attributed to a
    sub-agent role (the multi-agent features), else **v1** — always declaring the
    minimum version that covers what was emitted. Unmeasured fields are omitted
    (honest null), never guessed [docs/adapters.md].
    """

    def __init__(self) -> None:
        self._trajectory: list[dict] = []
        self._reasoning: list[dict] = []
        self._telemetry: dict[str, Any] = {}
        self._by_model: dict[str, dict] = {}
        self._used_agent = False
        self._finished = False
        self._finished_native = False

    # --- trajectory steps -----------------------------------------------------
    def _step(self, kind: str, *, agent: Optional[str], **fields: Any) -> None:
        step: dict[str, Any] = {"kind": kind}
        agent = _validate_agent(agent)
        if agent is not None:
            step["agent"] = agent
            self._used_agent = True
        for key, value in fields.items():
            if value is not None:
                step[key] = value
        self._trajectory.append(step)

    def message(
        self,
        text: str = "",
        *,
        agent: Optional[str] = None,
        relative_ts: Optional[float] = None,
        tokens: Optional[int] = None,
        cost: Optional[float] = None,
    ) -> "AgentLog":
        """Record a ``message`` step — the agent's own narration, in ``detail``."""
        self._step(
            "message", agent=agent, detail=text, relative_ts=relative_ts,
            tokens=tokens, cost=cost,
        )
        return self

    def tool_call(
        self,
        command: str = "",
        detail: str = "",
        *,
        exit_code: Optional[int] = None,
        files: Optional[list[str]] = None,
        agent: Optional[str] = None,
        relative_ts: Optional[float] = None,
        tokens: Optional[int] = None,
        cost: Optional[float] = None,
    ) -> "AgentLog":
        """Record a ``tool_call`` step (spawning a sub-agent is a tool_call)."""
        self._step(
            "tool_call", agent=agent, command=command, detail=detail,
            exit_code=exit_code, files_touched=files, relative_ts=relative_ts,
            tokens=tokens, cost=cost,
        )
        return self

    def file_edit(
        self,
        files: list[str],
        detail: str = "",
        *,
        agent: Optional[str] = None,
        relative_ts: Optional[float] = None,
        tokens: Optional[int] = None,
        cost: Optional[float] = None,
    ) -> "AgentLog":
        """Record a ``file_edit`` step over ``files_touched`` (``detail`` = the edit)."""
        self._step(
            "file_edit", agent=agent, files_touched=list(files), detail=detail,
            relative_ts=relative_ts, tokens=tokens, cost=cost,
        )
        return self

    def test_run(
        self,
        command: str,
        detail: str = "",
        *,
        exit_code: Optional[int] = None,
        agent: Optional[str] = None,
        relative_ts: Optional[float] = None,
        tokens: Optional[int] = None,
        cost: Optional[float] = None,
    ) -> "AgentLog":
        """Record a ``test_run`` step — only when the harness KNOWS it ran tests."""
        self._step(
            "test_run", agent=agent, command=command, detail=detail,
            exit_code=exit_code, relative_ts=relative_ts, tokens=tokens, cost=cost,
        )
        return self

    # --- flight recorder ------------------------------------------------------
    def reasoning(
        self,
        content: str,
        *,
        agent: Optional[str] = None,
        turn: Optional[int] = None,
        relative_ts: Optional[float] = None,
        tokens: Optional[int] = None,
        cost: Optional[float] = None,
    ) -> "AgentLog":
        """Record a reasoning span (the flight recorder — a SEPARATE artifact from
        the graded trajectory). ``turn`` links it to the 0-based trajectory-step
        index it belongs to; ``relative_ts`` timestamps it — both let operator
        views interleave thought and action [flight-recorder charter]."""
        entry: dict[str, Any] = {"content": content}
        agent = _validate_agent(agent)
        if agent is not None:
            entry["agent"] = agent
            self._used_agent = True
        if turn is not None:
            if turn < 0:
                raise ValueError(
                    f"turn must be a 0-based trajectory-step index, got {turn}"
                )
            entry["turn"] = turn
        for key, value in (("relative_ts", relative_ts), ("tokens", tokens), ("cost", cost)):
            if value is not None:
                entry[key] = value
        self._reasoning.append(entry)
        return self

    # --- terminal -------------------------------------------------------------
    def finish(
        self,
        *,
        cost: Optional[float] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        tokens_cache: Optional[int] = None,
        wall_time_s: Optional[float] = None,
        tool_calls: Optional[int] = None,
        by_model: Optional[dict] = None,
    ) -> dict:
        """Set whole-trial telemetry and write ``artifacts/agent_log.json``.

        Only non-``None`` fields update the telemetry block, so calling
        :meth:`finish` again (as :func:`run_visible` does on error) never
        overwrites a previously-reported value with a null. ``by_model`` maps a
        DECLARED model id to a telemetry-shaped dict (v2 ``telemetry_by_model``);
        the whole-trial block stays the sole authoritative stream. Returns the log
        dict that was written.

        Refuses (``RuntimeError``) after :meth:`finish_native`: the two terminals
        write ONE file in incompatible formats, so a generic rewrite would clobber
        the native evidence — a programming error, not a runtime condition.
        """
        if self._finished_native:
            raise RuntimeError(
                "finish() called after finish_native(): a native-format log was "
                "already written verbatim; a generic rewrite would clobber the "
                "native evidence (one file, one format) [docs/adapters.md]"
            )
        for field, value in (
            ("cost", cost),
            ("tokens_in", tokens_in),
            ("tokens_out", tokens_out),
            ("tokens_cache", tokens_cache),
            ("wall_time_s", wall_time_s),
            ("tool_calls", tool_calls),
        ):
            if value is not None:
                self._telemetry[field] = value
        if by_model is not None:
            self._by_model = {
                model: {k: v for k, v in block.items() if v is not None}
                for model, block in by_model.items()
            }
        self._finished = True
        log = self._build()
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        AGENT_LOG_PATH.write_text(json.dumps(log), encoding="utf-8")
        return log

    def finish_native(self, raw: str) -> dict:
        """Persist a NATIVE-format result log VERBATIM and terminate [docs/adapters.md, EVAL-4 AC-2].

        The sanctioned terminal for a ``platform: claude_code``-style arm, whose
        adapter (``speaks_generic_format=False``) parses the underlying stack's OWN
        result JSON rather than the verdi generic format this class otherwise writes.
        ``raw`` is that result object's text (e.g. the CLI's ``--output-format json``
        stdout); it is written to ``artifacts/agent_log.json`` byte-for-byte — evidence
        is read, never reconstructed, so NO re-serialization touches it.

        Fails loudly (``ValueError``) if ``raw`` is not a JSON object: a native log
        that is not an object would corrupt the harness read as ``telemetry_corrupt``
        [RN-17] instead of yielding honest telemetry. Returns the parsed dict.
        :meth:`finish` is forbidden thereafter — one file, one format.
        """
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"native log is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError(
                f"native log must be a JSON object, got {type(parsed).__name__}"
            )
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        AGENT_LOG_PATH.write_text(raw, encoding="utf-8")
        self._finished = True
        self._finished_native = True
        return parsed

    def _build(self) -> dict:
        """The generic-format log dict (pure — no I/O), for reuse in tests."""
        version = 2 if (self._by_model or self._used_agent) else 1
        log: dict[str, Any] = {VERSION_KEY: version}
        if self._telemetry:
            log["telemetry"] = dict(self._telemetry)
        if self._trajectory:
            log["trajectory"] = list(self._trajectory)
        if self._reasoning:
            log["reasoning"] = list(self._reasoning)
        if self._by_model:
            log["telemetry_by_model"] = dict(self._by_model)
        return log


def run_visible(main: Callable[[AgentLog], Any]) -> None:
    """Run ``main(log)`` so that a trial is ALWAYS scorable [refactor 03 §1, §2].

    Hands ``main`` a fresh :class:`AgentLog`; whatever ``main`` records is
    preserved even if it then raises. On any error this appends a fail-visible
    ``agent error: …`` message, writes the accumulated log, prints the error to
    stderr, and exits 1 — a nonzero agent exit is still a COMPLETED, gradeable
    trial for verdi (the runner reserves 124/125). On success it guarantees the
    log was written even if ``main`` forgot to call :meth:`AgentLog.finish`.

    Native terminal: when ``main`` already terminated via
    :meth:`AgentLog.finish_native`, the error path does NOT append/rewrite a generic
    log — the native file IS the evidence, and a generic rewrite would clobber it.
    The failure still stays visible via the stderr print, exit 1, and the native
    log's own ``is_error`` flag.
    """
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    log = AgentLog()
    try:
        main(log)
    except BaseException as e:  # fail VISIBLY, but still leave a scorable log
        if not log._finished_native:
            log.message(f"agent error: {type(e).__name__}: {e}")
            log.finish()
        print(f"verdi_agent: agent FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    if not log._finished:
        log.finish()
