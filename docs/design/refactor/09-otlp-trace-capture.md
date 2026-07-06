# 09 — In-trial OTLP trace capture (Phase 3)

The product goal: **a trial's spans are first-party evidence.** Any agent
stack that speaks OpenTelemetry — LangChain/LangSmith, pydantic-ai, Logfire,
OpenLLMetry, a hand-instrumented SDK — can emit OTLP inside the trial
container and have its spans land in the workspace as a redacted,
canonicalized, sha-ledgered artifact, without a single byte leaving the
hermetic boundary. verdi-bench never builds instrumentation; it builds the
mailbox.

Today there is no span ingestion at all — the capability audits record OTel's
absence (`docs/design/review/verdi-bench-production-readiness-audit.md:411,455`,
`verdi-bench-capability-audit-2026-07.md:576-579`), and the only telemetry
input is `artifacts/agent_log.json`. Stacks that already produce rich span
trees must flatten them by hand into the generic log or lose the data.

Rent-vs-build rule this spec implements: **if bytes end up under the hash
chain or in front of the judge, verdi-bench must have captured them.**
Upstream emission (OTel SDKs) and downstream browsing (LangSmith et al.) are
rented; capture, canonical bytes, and the sha are ours.

**DECISIONS:** A11 (engine/run-config OTLP fields), A12 (two new
`failure_reason` values + fake-engine parity), A13 (`spans_sha` on the trial
event), A14 (optional `opentelemetry-proto` dependency), D-09-1 (raw
collector-log retention: **delete on teardown, `--keep-raw` opt-in**) — all
accepted 2026-07-06, recorded in `decisions.ndjson`.

## 1. Architecture

```
 trial container ──POST /v1/traces──▶ verdi-trace-collector ──▶ shared envelope
 (any OTel SDK;    OTLP/HTTP,          (hermetic sidecar,        JSONL on host
  OTEL_* env       protobuf or json    METERED_NETWORK only,     (operator-tier)
  injected by      x-verdi-trial hdr)  never parses bodies)
  the engine)                                                        │
                                              engine post-run ladder │ per-trial
                                              _read_span_log ────────┘ filter+decode
                                                    │
                              workspace artifacts/otlp_spans.json  (OTLP-JSON)
                                                    │
                    seam.py redaction ──▶ persist (scrub→canonical→sha→readback)
                                                    │
                                     spans_sha hoisted on the `trial` event
```

Design invariants, chosen deliberately:

- **Sidecar endpoint, not file export.** Every OTel SDK can point
  `OTEL_EXPORTER_OTLP_ENDPOINT` at an HTTP collector; almost none has a
  portable file exporter. Zero agent-code changes is the whole value.
- **The collector is a dumb receiver.** It appends raw request bodies to an
  envelope log and returns 200. Decoding happens harness-side, post-trial,
  deterministically. Raw bytes are the evidence; interpretation is replayable.
- **Config rides standard OTel env vars, not `request.json`.** The frozen
  `TrialRequestFile` contract (`harness/run/request.py:27-41`, A1) is
  untouched — images that ignore OTel see nothing new.
- **Normalization into trajectory/flight-recorder is a separate concern** —
  spec [10](10-span-trajectory-normalization.md). This spec ends at the
  redacted, sha-bound `otlp_spans.json` artifact.

## 2. The collector container — `harness/hermetic/_collector_container.py`

A stdlib-only single file, sibling of `_proxy_container.py`
(`harness/hermetic/_proxy_container.py:1-18`): mounted read-only into the
pinned `python:3.12-alpine` base (`metering.py:39-42` pattern), never
imported by the harness, no image build.

Behavior:

- Listens on `0.0.0.0:4318` (the OTLP/HTTP standard port), thread-per-
  connection like the proxy (`_proxy_container.py:104-113`).
- Accepts `POST /v1/traces` with `Content-Type: application/x-protobuf` or
  `application/json`, honoring `Content-Encoding: gzip`. Everything else
  (including `/v1/logs`, `/v1/metrics` in v1) → 404.
- **Never parses span payloads.** Each accepted request appends one envelope
  line to the log:

  ```json
  {"trial": "<id>", "seq": 41, "content_type": "application/x-protobuf", "body_b64": "..."}
  ```

  `seq` is a process-lifetime counter — **no wall-clock timestamps in the
  envelope** (determinism; spans carry their own times). JSON bodies are
  embedded as `body_json` un-decoded-but-inline; protobuf as `body_b64`.
- **Trial attribution** via a request header `x-verdi-trial: <trial_id>`,
  injected by the engine through `OTEL_EXPORTER_OTLP_HEADERS` (the standard
  mechanism every SDK honors) — the collector's analog of the proxy's
  trial-id-as-userinfo (`_proxy_container.py:41-49`). A request without the
  header is recorded with `"trial": "-"` and answered 400 — logged, excluded
  from every trial's extraction, countable by operators. Fail loud, lose
  nothing.
- Log path from `COLLECTOR_LOG` env, default `/var/log/verdi/otlp.jsonl` —
  the custom-basename lesson is applied from day one: the host-side lifecycle
  passes the *basename* through the env var and mounts the parent directory,
  exactly the `PROXY_LOG` fix in `metering.py:86-98,143-145` (commit
  `988af58`), so a custom log path can never silently fail open. The log is
  pre-touched so a zero-span trial still finds a present log
  (`metering.py:127-130` pattern).

## 3. Lifecycle — `harness/hermetic/tracing.py`

```python
MANAGED_COLLECTOR_NAME = "verdi-trace-collector"   # single-constant discipline,
COLLECTOR_PORT = 4318                              # like METERED_NETWORK

class CollectorConfig(BaseModel):                  # extra="forbid"
    endpoint: str        # "http://verdi-trace-collector:4318"
    log_path: str        # host-side envelope JSONL

class TraceCollector:
    @classmethod
    def managed(cls, *, log_path: Path | None = None) -> "TraceCollector": ...
    def __enter__(self) -> CollectorConfig: ...    # ensure network, run hardened
                                                   # detached container, readiness
                                                   # probe on 4318 (no sleep-N)
    def __exit__(self, *exc) -> None: ...          # teardown, always
```

Mirrors `MeteringProxy` (`harness/hermetic/metering.py:69-202`) exactly, with
one deliberate difference: the collector attaches **only** to
`METERED_NETWORK` (`--internal`) and never to the egress network — it has no
outbound needs, so span data physically cannot leave the host. Trial
containers already join `METERED_NETWORK` when a proxy is configured
(`harbor.py:279-285`); the collector is reachable there by name.

Surfacing follows the established triad ([04](04-run-engine.md) §1):

| Surface | Form |
|---|---|
| SDK | `TraceCollector` re-exported by `harness/sdk` (owner stays `hermetic`, per [02](02-experiment-sdk.md) §1 layering) |
| Operators | `bench otlp up` / `bench otlp down` in `harness/hermetic/cli.py`, beside `bench proxy up/down` |
| Config | additive `otlp.managed: true` on `RunConfigFile` ([04](04-run-engine.md) §4) |

## 4. Engine integration

**Request plumbing (A11).** `TrialRequest` (`harness/run/types.py`) gains an
optional `otlp: OtlpConfig | None = None` beside `proxy` — `OtlpConfig`
carries `endpoint` and `log_path`. `None` means "no collector configured":
zero behavior change anywhere.

**Env injection (harbor).** When `request.otlp` is set, `build_run_command`
(`harbor.py:251-309`) adds:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://verdi-trace-collector:4318
OTEL_EXPORTER_OTLP_HEADERS=x-verdi-trial=<trial_id>
NO_PROXY=verdi-trace-collector          # appended, not replaced
```

The `NO_PROXY` entry is load-bearing: `HTTP(S)_PROXY` is already injected for
metered trials (`harbor.py:279-285`), and without it OTel exporters would
route span posts *through the metering proxy*, polluting the egress picture
and failing the allowlist. A contract test pins this.

**Post-run ladder.** `EngineBase` gains `_read_span_log(req)` as a shared
sibling of `_scan_proxy_log` (`base.py:208-259`), running for every engine:

1. `request.otlp is None` → return "not configured" (no artifact, no sha).
2. Configured but envelope log missing → raise `SpanLogMissingError` —
   fail-closed, the PRA-H4 discipline (`base.py:232-237`), surfacing as new
   `failure_reason` value **`span_log_missing`** (A12). A configured
   collector whose output vanished is infrastructure breakage, never "zero
   spans".
3. Filter envelope lines by `rec["trial"] == request.trial_id` (the
   `_scan_proxy_log` selection rule, `base.py:249`), decode (§5), and write
   `artifacts/otlp_spans.json` into the workspace — before `seam.py`'s
   whole-workspace redaction pass (`seam.py:171`), so span payloads are
   scrubbed by the same machinery as every other artifact.
4. Zero matching lines → write the artifact with an empty `batches` list:
   honest emptiness ("collector ran, this trial emitted nothing"), distinct
   from absence ("no collector configured").

Ladder precedence: `span_log_missing` slots **after** `proxy_log_missing` in
the frozen downgrade order (`kill_failed > daemon_error > timeout >
telemetry_corrupt > proxy_log_missing > span_log_missing`) — egress evidence
outranks telemetry evidence. This is a closed-vocabulary change (A12).

**Fake-engine parity (A10 precedent).** When `otlp` is configured,
`FakeEngine` writes scripted envelope lines keyed to the trial (the exact
pattern of its proxy-log simulation, `fake.py:81-89`) and inherits
`_read_span_log` unchanged, so the cross-engine contract suite
(`tests/test_eval4_seam.py:44-69`) exercises both engines through one code
path — including the fail-closed branch.

## 5. Decoding and the artifact contract

`harness/hermetic/otlp_decode.py` turns envelope lines into OTLP-JSON:

- `application/json` bodies are parsed and structurally validated.
- `application/x-protobuf` bodies are decoded with `opentelemetry-proto`
  (+`protobuf`), shipped as the **optional extra** `verdi-bench[otlp]`
  (A14). The import is lazy; a protobuf envelope encountered without the
  extra installed raises with an actionable message — configured capture
  never silently degrades.

The workspace artifact:

```json
{
  "schema_version": 1,
  "trial_id": "…",
  "batches": [ {"content_type": "…", "resource_spans": [ … ]} ]
}
```

`OtlpCaptureRecord` is `extra="forbid"` on the wrapper; `resource_spans` is
externally-shaped OTLP-JSON and passes through intact (it is evidence, not
our schema). Constants: `SPANS_SCHEMA_VERSION = 1`,
`SPANS_FILENAME = "otlp_spans.json"`.

**Canonical bytes** use the house recipe (`trajectory.py:133-141`:
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, UTF-8) —
**plus `allow_nan=False`**, closing the asymmetry the trajectory recipe has
against `ledger/chain.py:46-58`: span payloads are float-rich, and a NaN
must fail loudly rather than emit non-interoperable JSON. Persistence goes
through the same scrub→revalidate→write→readback-verify door as
`persist_trajectory` (`trajectory.py:148-187`) — via
`persist_versioned_artifact` once [04](04-run-engine.md) §3 lands, as a
third near-clone until then.

**Ledger binding (A13).** `TrialRecord` gains additive `spans_sha:
str | None`; `_reshape_trial` (`events.py:110-125`) hoists it beside
`trajectory_sha`/`flight_recorder_sha`; the `TRIAL` `EventSpec` adds it to
`omit_if_none` (`events.py:216-218`) so every existing trial event's bytes
are unchanged. Readers take the sha from the event, never the round-tripped
record (`events.py:409-421` rule). `LedgerView.trials()` /`TrialEventView`
([06](06-ledger-telemetry.md) §1) grow the field; a `resolve_spans(...)`
verifier mirrors `resolve_trajectory` (`trajectory.py:231-261`): `verified`
only when `sha256(bytes) == ledgered_sha`. The Phase-0 constructor-replay
golden ([01](01-safety-nets.md) §2) is extended with the new field both ways
(present / omitted).

## 6. Hermeticity, blinding, and compliance analysis

- **No new egress.** The collector is internal-network-only; span posts
  terminate on the host. The metering proxy's allow/deny picture is
  unchanged (guaranteed by the `NO_PROXY` injection + contract test).
- **`bench images verify` is unaffected.** Verify runs `--network none` with
  no `OTEL_*` env ([03](03-images-and-environments.md) §5); OTel SDKs buffer
  and drop unexported spans by design. Compliance never requires spans.
- **Secrets.** The per-trial artifact is written before the workspace
  redaction pass and scrubbed again inside persistence — the same double
  door as the trajectory. The *shared host-side envelope log* however holds
  raw (possibly secret-bearing, possibly identity-bearing) bodies.
  **DECISION D-09-1:** default = the lifecycle deletes the envelope log on
  `__exit__` after trials have extracted their slices; `--keep-raw`
  (`bench otlp up --keep-raw` / `TraceCollector.managed(keep_raw=True)`)
  retains it as an explicitly operator-tier file. Recommendation: default
  delete — the ledgered per-trial artifact is the evidence of record.
- **Blinding.** Raw spans inevitably name models and vendors
  (`gen_ai.request.model`, `service.name`). `otlp_spans.json` is therefore
  **operator/forensics-tier**: it must never enter the judge packet, and no
  `Packet`/`ResponseArtifacts` field is added by this spec — the D5
  field-coverage meta-test ([05](05-grading-judging.md) §5,
  [01](01-safety-nets.md) D5) enforces that any future promotion into the
  packet joins both the identity and secret scans. Identity-safe projection
  into judge-adjacent form is exactly spec [10](10-span-trajectory-normalization.md)'s job.
- **Structural contracts.** New modules join the `.importlinter` source
  lists in the same commit (A5 mechanics, forced by
  `tests/test_import_contracts.py:40-60`). `hermetic` continues to name no
  engine (`04:47-49`); `grade`/`judge` import neither `otlp_decode` nor
  `opentelemetry-proto` (extend the grade-has-no-LLM-clients contract's
  forbidden list). Only `hermetic` talks to Docker (A6) — the collector
  lifecycle lives there and nowhere else.

## 7. Migration steps

1. `_collector_container.py` + unit tests against a live socket (envelope
   framing, gzip, 400-on-unattributed, `COLLECTOR_LOG` basename honor).
2. `tracing.py` lifecycle + `bench otlp up/down`; teardown-on-failure and
   custom-basename regression tests (the `988af58` class).
3. `OtlpConfig` on `TrialRequest` + `RunConfigFile.otlp` + harbor env
   injection (incl. `NO_PROXY`) + fake-engine scripted envelopes.
4. `otlp_decode.py` + `OtlpCaptureRecord` + persistence + `spans_sha`
   ledger plumbing (A13 golden extension in the same commit).
5. `_read_span_log` in the ladder + `span_log_missing` vocabulary insertion
   (A12) + cross-engine contract-suite rows.
6. SDK re-export; shakedown recipe: the official-image campaign wraps
   `ws.run` in `TraceCollector.managed(...)` beside `MeteringProxy.managed(...)`
   ([08](08-shakedown-and-tests.md) north-star shape).
7. Docs: `docs/images.md` gains an "emitting spans" section (env vars the
   image may read; explicitly optional); `docs/engines.md` gains the span-log
   ladder step.

## 8. Constraining tests

- Envelope determinism: same request sequence → byte-identical log (no
  timestamps, seq-only).
- Trial filtering: interleaved multi-trial envelopes extract exactly by id;
  `"-"` lines never attach to any trial.
- Fail-closed: configured + missing log → `span_log_missing`, both engines;
  configured + zero spans → empty-batches artifact + sha present.
- NaN in a span float → loud persistence failure (allow_nan=False).
- `NO_PROXY` pin: metered trial with collector configured produces zero
  collector-bound lines in the proxy log.
- Golden: fixture envelope log → byte-exact `otlp_spans.json` → pinned sha;
  constructor-replay golden covers `spans_sha` present and omitted.
- Docker-marked e2e: a container running the real `opentelemetry-sdk`
  exports through the collector; artifact verifies via `resolve_spans`.
