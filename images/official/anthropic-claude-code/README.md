# anthropic-claude-code (official image)

Drives the version-pinned **Claude Code** CLI as a verdi trial agent. Extends
`verdi-base`; `agent.py` reads `/verdi/request.json`, invokes `claude
--output-format json` over the task inside `/workspace`, and persists the CLI's
own result object verbatim as the native `artifacts/agent_log.json`. The arm runs
as `platform: claude_code`, so the adapter reads tokens/cost/wall-time from that
report. See `docs/images.md` §1 for the contract.

```bash
bench images build anthropic-claude-code --pin
bench images verify <pinned-ref>
```

## What is pinned

The CLI version is part of the arm's identity — the Dockerfile pins
`@anthropic-ai/claude-code` via the `CLAUDE_CODE_VERSION` build arg (default a
recent version). Pin the version you tested and echo it in the arm's `payload`
and the image tag; never `latest`.

## Honesty notes

- **The build needs network** (apt + npm) — this is a stack install, not a
  stdlib-only agent. The RESULT is still digest-pinned and harbor runs it offline
  with `--pull=never`. In a networked environment the image builds cleanly (the
  default pinned version installs); **confirm the pin and the CLI's flag spelling
  against your target** — both are version-coupled and are NOT re-verified by the
  offline `bench images verify`.
- **`bench images verify` proves plumbing, not intelligence.** Offline, with no
  keys, the `claude` CLI cannot reach a model, so it fails — and `run_visible`
  still writes a scorable `agent_log.json` and exits nonzero. That is exactly what
  verify checks (request in → scorable log out). The real coding behavior is
  exercised only WITH keys + network.
- **Telemetry comes from the CLI's own report.** With `--output-format json` the
  CLI emits a native result object (tokens/cost/duration); the agent persists it
  verbatim and the `claude_code` adapter reads it — nothing translated, nothing
  guessed [D004]. Fields the report omits (e.g. per-step tokens) stay null. The
  generic log is the FALLBACK for the plumbing-failure path only: a CLI that dies
  before emitting JSON (absent/unauthenticated/offline) leaves a scorable generic
  error log; a CLI that exits 0 without its JSON contract gets no log rather than a
  fabricated one.
