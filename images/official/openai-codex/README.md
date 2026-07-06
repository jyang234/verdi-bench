# openai-codex (official image)

Drives the version-pinned **OpenAI Codex** CLI as a verdi trial agent. Extends
`verdi-base`; `agent.py` reads `/verdi/request.json`, invokes `codex exec` over
the task inside `/workspace`, and emits `artifacts/agent_log.json` in the verdi
**generic** format via `verdi_agent`. See `docs/images.md` §1 for the contract.

```bash
bench images build openai-codex --pin
bench images verify <pinned-ref>
```

## What is pinned

The CLI version is part of the arm's identity — the Dockerfile pins `@openai/codex`
via the `CODEX_VERSION` build arg. Pin the version you tested and echo it in the
arm's `payload` and the image tag; never `latest`.

## Honesty notes

- **The build needs network** (apt + npm). The RESULT is still digest-pinned and
  harbor runs it offline with `--pull=never`. **Confirm the pinned version and the
  CLI's exec-mode flag spelling against your target** — the `CODEX_VERSION` default
  is a starting point, both are version-coupled, and neither is re-verified by the
  offline `bench images verify`. If the pinned version does not resolve on npm in
  your environment, set `--build-arg CODEX_VERSION=<a version that does>`.
- **`bench images verify` proves plumbing, not intelligence.** Offline, with no
  keys, the `codex` CLI does not fail fast — it HANGS on network/auth (unlike
  `claude`, which errors immediately). `verify`'s synthetic request carries a short
  `payload.cli_timeout_s`, which `agent.py` honors, so the CLI subprocess times out
  inside `run_visible` and a scorable `agent_log.json` is still written well within
  verify's window (request in → scorable log out). The real coding behavior is
  exercised only WITH keys + network.
- **Telemetry is null by design here** — verdi never guesses [D004]. For per-trial
  telemetry, run the arm as `platform: codex` (the native adapter), the documented
  native-emission alternative, rather than fabricating a native session.
