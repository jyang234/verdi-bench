# Multi-agent reference image

A **worked reference** for running a multi-agent *workflow* agent stack under
verdi's harbor engine. It shows the one thing that trips people up: a workflow —
an orchestrator that decomposes a task and dispatches sub-agents — runs as **one
verdi trial inside one image**. The sub-agents are the agent's *internal*
business; verdi observes the boundary (prompt in → graded workspace + log out)
and lets the workflow *report its own sub-structure* through the log.

```
request.json (prompt + arm)  ─▶  [ ONE container: orchestrator → planner → worker-1/worker-2 ]  ─▶  /workspace + agent_log.json
                                    └──────────────── the whole workflow lives here ─────────────┘
```

## What it demonstrates

`agent.py` runs a genuine (minimal) workflow and emits `artifacts/agent_log.json`
in the verdi **generic v2** format, self-reporting its sub-structure:

- **`reasoning`** with a per-entry **`agent`** role [EVAL-24 AC-6] — the planner's
  decomposition reasoning, each worker's reasoning, the orchestrator's aggregation.
- **`trajectory`** with a per-step **`agent`** role [EVAL-21] — who did what.
- **`telemetry_by_model`** — per-model spend summed across the sub-agents.

verdi then slices reasoning/trajectory by sub-agent (`slice_reasoning_by_agent`,
`slice_by_agent`) and renders per-role reasoning in the operator compare view.

## The harbor compliance contract (what any image must satisfy)

> The normative statement of this contract is `docs/images.md` §1; the table
> below is the worked, per-image view. This reference extends `verdi-base`, so the
> request reader, the CONNECT-tunnel egress dance, and the generic-log writer all
> come from `verdi_agent` and `agent.py` is multi-agent orchestration only.

| Requirement | How this image meets it |
|---|---|
| Digest-pinned, pre-baked, offline (`docker run --pull=never`) | stdlib-only, no build-time network; pin the local image id |
| Reads `/verdi/request.json` (read-only, outside `/workspace`) | `main()` reads `{prompt, arm, model, payload}` |
| Writes the solution to `/workspace` + telemetry to `/workspace/artifacts/agent_log.json` | orchestrator aggregates → `solution.py` + the generic v2 log |
| Runs as an arbitrary `uid:gid`, only writes under `/workspace` + `/verdi` | no root assumptions; writes only `/workspace` |
| Hardened runtime (`--cap-drop ALL`, `--pids-limit`, cpu/mem quotas) | plain Python, no privileged ops |
| Network via the metering proxy (`HTTP(S)_PROXY` + per-trial credential on CONNECT) | `post_json` tunnels + sends `Proxy-Authorization` |
| Provider keys via allowlisted `--env` | reads `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` from env, never baked in |
| Fail-visible | any error still writes an `agent_log` (absent-honest) before re-raising |

## One image, both arms — same substrate

verdi assigns the image **per task**, shared across the paired arms (the image is
the substrate; the *arm* is the treatment, via `request.json`). So compare
**workflow configs** by parameterizing on the arm (`payload`/`model`), both arms
running this same digest — never a different image per arm (that would confound
the stack with the container). For a cross-*stack* A/B, extend this pattern so
one image dispatches both stacks on `request.arm`.

## Build, pin, use

```bash
# builds verdi-base first (this image's FROM depends on it), then this context,
# and pins the result to a sha256 digest:
bench images build images/reference/multi-agent --pin
bench images verify <pinned-ref>        # offline compliance check, no keys
# harbor runs the digest-pinned local image; reference it as the task's image:
#   tasks.yaml:  - id: t1   image: <pinned-ref>   ...
```

## Verified compliant

`tests/test_eval24_multi_agent_reference.py` imports this image's **pure**
`build_agent_log` and pushes its output through verdi's real parsers
(`normalize_reasoning`, `normalize_trajectory`, `normalize_generic_by_model`,
`slice_reasoning_by_agent`) and the persist redaction door — proving the emitted
log is harbor/EVAL-21/EVAL-24 compliant **without** docker or real keys.
