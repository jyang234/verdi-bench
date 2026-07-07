# verdi-base

The maintained base image every verdi trial image extends [refactor 03 §3]. It
bakes in every harbor compatibility concern so writing a trial image means
writing **agent logic only** — extend this base, drop in an `agent.py`, and
`bench images verify` proves compliance without reading engine source.

```
FROM verdi-base
COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
```

## What the base provides

- **`verdi_agent`** — the stdlib-only in-image SDK on `PYTHONPATH` (`import
  verdi_agent`): `read_request()`, `post_json()` (the CONNECT-tunnel +
  `Proxy-Authorization` dance, done once), `AgentLog` (the generic v1/v2 log
  writer), and `run_visible(main)` (fail-visible: any error still leaves a
  scorable log and exits 1). Version-stamped as `verdi_agent.VERDI_AGENT_VERSION`.
- **The fixed workspace layout** (below), created and `--workdir`-ed.
- **A hardened default posture**: `python:3.12-slim` (glibc — see the Dockerfile
  for why not alpine), `PYTHONDONTWRITEBYTECODE=1`, a non-root default `USER`, and
  `HOME=/tmp` so the container survives an arbitrary `uid:gid`.

## Workspace layout (fixed by the harbor contract)

| Path | Mode | Purpose |
|---|---|---|
| `/workspace` | rw, bind-mounted, `--workdir` | the graded workspace — write the solution here |
| `/workspace/artifacts/agent_log.json` | rw | telemetry in the verdi generic v1/v2 format (or a registered native format) |
| `/verdi/request.json` | **read-only**, outside `/workspace` | the task + arm config (`schema_version`, `prompt`, `arm`, `model`, `payload`) |

## The compatibility contract (normative)

Every trial image must obey this. `docs/images.md` is the single canonical
statement; it is reproduced here for the image author and is the same contract
`bench images verify` enforces.

| Obligation | Baked in / enforced by |
|---|---|
| Read `/verdi/request.json` (`schema_version`, `prompt`, `arm`, `model`, `payload`) — read-only, outside `/workspace` | `verdi_agent.read_request()`; `verify` mounts a synthetic request read-only |
| Write the solution into `/workspace`; telemetry to `/workspace/artifacts/agent_log.json` in generic v1/v2 or a registered native format | `verdi_agent.AgentLog.finish()`; `verify` parses it under the declared format via the real `harness.adapters.generic` parsers |
| Egress ONLY via `HTTP(S)_PROXY`, with per-trial basic-auth on the `CONNECT` (trial id as userinfo; stdlib will not add it) | `verdi_agent.post_json()` |
| Survive arbitrary `uid:gid`, `--cap-drop ALL`, `--security-opt no-new-privileges`, pids/mem quotas, `--pull=never`, digest pinning | base posture (non-root `USER`, `HOME=/tmp`, world-writable `/workspace`); `verify` runs non-root with `--network none` |
| Exit-code semantics: a nonzero agent exit is still a **completed** (scorable) trial; 124/125 are runner-reserved | `verdi_agent.run_visible()` exits 1 with a scorable log |
| Fail visibly but leave a scorable `agent_log.json` | `verdi_agent.run_visible()` |
| Write **nothing outside `/workspace`** | `verify` asserts it (no host or `/verdi` writes) |
| Multi-agent: v2 log with the closed `agent` role vocabulary, `telemetry_by_model` keyed by declared models, and `reasoning` entries | `verdi_agent.AgentLog` (`agent=` / `by_model=` / `reasoning()`); the harness generic parsers |

## Build and verify

```bash
bench images build base --pin          # build verdi-base, print its sha256 digest
bench images build generic-llm --pin   # an official image FROM verdi-base
bench images verify <pinned-ref>       # offline compliance check (no keys, no LLM)
```

`bench images verify` runs the image with `--network none`, a synthetic
`/verdi/request.json`, a tight timeout, and a non-root `uid:gid`, then asserts
`artifacts/agent_log.json` exists and parses under the declared format, exit
semantics are honored, and nothing was written outside `/workspace`. It validates
**plumbing, not intelligence** — no provider keys, no model calls.
