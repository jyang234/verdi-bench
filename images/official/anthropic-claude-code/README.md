# anthropic-claude-code (official image)

Drives the version-pinned **Claude Code** CLI as a verdi trial agent. Extends
`verdi-base`; `agent.py` reads `/verdi/request.json`, invokes `claude` over the
task inside `/workspace`, and emits `artifacts/agent_log.json` in the verdi
**generic** format via `verdi_agent`. See `docs/images.md` §1 for the contract.

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
- **Telemetry is null by design here.** The print-mode CLI does not self-report
  tokens/cost in a stable machine form, and verdi never guesses [D004]. For
  per-trial telemetry, run the arm as `platform: claude_code` (the native adapter)
  or parse the CLI's JSON output — the documented native-emission alternative. This
  agent deliberately emits the honest generic minimum rather than a fabricated
  native session.
