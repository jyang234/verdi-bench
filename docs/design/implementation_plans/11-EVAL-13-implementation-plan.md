# EVAL-13 implementation plan — live observability

Builds the four seams the 2026-07-04 assessment found missing, in dependency
order, each milestone landing with its AC tests (spec:
`docs/design/specs/eval13.spec.md`; decisions: `eval13.decisions.ndjson`).

## M1 — run heartbeat (AC-1)

- `harness/run/heartbeat.py`: `RunHeartbeat` — holds the sidecar path, the
  `EventContext` (clock + experiment id), planned-cell count, and ceiling;
  every state change rewrites the whole document via write-temp +
  `os.replace` (atomic; no fsync — ephemeral operational file, torn reads
  impossible by rename semantics, durability deliberately not promised).
- Wire into `harness/run/interleave.schedule` behind an optional
  `heartbeat_path` parameter (None ⇒ no heartbeat; existing callers and the
  one-event entrypoint sweep unchanged). Write points: after resume-state
  derivation (`running`), before each attempt inside the infra-rerun loop
  (`in_flight` with attempt number), after each completed trial / infra
  failure (counters), and in the existing `finally` (terminal state chosen
  from `stopped_cost_ceiling`). Heartbeat write failures propagate — same
  fail-loud fate as the ledger appends beside them.
- `harness/run/cli.py` passes `experiment_dir / "run.heartbeat.json"`.

## M2 — ledger tail cursor (AC-2)

- `harness/ledger/query.py`: `tail_events(path, offset=0) ->
  (list[dict], int)` — read bytes from `offset`, consume only through the
  last `\n`, parse each complete line (malformed JSON in a complete line
  raises — fail loud), return the next byte offset. `offset > size` raises
  (a shrunken ledger is rewrite evidence, not a reset); absent file returns
  `([], 0)`.

## M3 — status subsystem (AC-3, AC-4)

- `harness/status/aggregate.py`: `compute_status(experiment_dir) -> dict` —
  chain verdict first (read-side `verify`); on failure return
  `chain.ok=false` + `stages=None` (fail closed), heartbeat still surfaced.
  On success assemble: lock summary (genesis event), planned cells via
  `enumerate_trials` × spec arms/reps × `tasks.yaml` ids, done/infra from
  trial events, per-arm outcome counts, spend (telemetry cost else
  proxy-metered, the RN-2 enforcement figure) vs ceiling, grade progress
  (grade / terminal-vs-transient cant_grade via the EVAL-5 vocabulary),
  judge verdict counts, review packet/human-verdict/reveal counts, process
  scores, forensics report count + latest flags/coverage, quarantines,
  contamination probes, selfcheck state via EVAL-6 `selfcheck_status`,
  latest renders, and the parsed heartbeat (or None).
- `harness/status/cli.py`: `bench status <dir> [--json]`, read-only, no
  event appended; registered in `harness/cli.py`'s stage-command list.

## M4 — serve subsystem (AC-5, AC-6)

- `harness/serve/server.py`: stdlib `ThreadingHTTPServer`; GET-only handler
  routing `/` → operator page, `/api/status` → M3, `/api/events?offset=N` →
  M2, `/api/timeline` → EVAL-12 `trial_timeline`; 404 unknown, 405 non-GET;
  default bind `127.0.0.1`.
- `harness/serve/page.py`: `OPERATOR_PAGE` — one self-contained HTML string
  (inline CSS + inline script, relative `fetch('/api/…')` only, no external
  references) with the standing unblinded-operator disclosure banner.
- `harness/serve/cli.py`: `bench serve <dir> [--host] [--port]`.

## M5 — contracts + docs (AC-7)

- `.importlinter`: add `harness.status`, `harness.serve` (+
  `harness.run.heartbeat`) to the harbor-confinement source list; add
  `harness.status`, `harness.serve` to the ledger-writes-only-via-events
  source list; new `observability-llm-free` forbidden contract
  (status/serve ↛ judge providers/client).
- README: document `bench status` / `bench serve` in Usage (XC-7 test),
  bump the import-linter contract count claim to 6.
- `make verify` green; no new entrypoints, no new event kinds.
