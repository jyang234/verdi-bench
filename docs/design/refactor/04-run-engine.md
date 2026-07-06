# 04 — Execution: hermetic layer, engines, run seam (Phases 3–5)

**DECISIONS required:** A6 (re-document the "only harbor talks to Docker"
claim); A10 (fake-engine fail-closed proxy-log parity).

## 1. `harness/hermetic` — one owner for Docker mechanics (Phase 3)

Docker argv/daemon interaction is hand-rolled in three places inside the
harness alone — `harbor.py:236-294` (trial), `grade/container.py:305-344`
(grader + plugins, same cap-drop/no-new-privileges/uid:gid recipe),
duplicated daemon probes and exit-125 semantics — which already falsifies
harbor.py's "only this module talks to Docker" docstring (**A6**: re-scope
the claim to the new layer honestly, don't reword silently). Shakedown adds
a fourth and fifth copy (`proxy_up`/`sh` in two scripts).

```python
# harness/hermetic/docker.py
class DockerClient:                     # promotes harbor's CommandRunner seam
    def run(self, argv, *, timeout_s=None) -> CompletedProcess: ...
    def daemon_available(self) -> bool: ...
class HardenedCommand:                  # the shared recipe, one place:
    ...                                 # cap-drop ALL, no-new-privileges,
                                        # uid:gid, pids/mem=swap quotas,
                                        # --pull=never, ro mounts, workdir
# harness/hermetic/network.py
METERED_NETWORK = "verdi-metered"       # THE constant; harbor + compose file +
                                        # scripts import it instead of restating
# harness/hermetic/metering.py
class MeteringProxy:                    # context manager
    @classmethod
    def managed(cls, allow: list[str], *, log_path: Path | None = None) -> "MeteringProxy": ...
    def __enter__(self) -> ProxyConfig: ...   # creates metered+egress networks,
                                              # runs the pinned proxy image with the
                                              # allowlist injected, provisions the
                                              # log, waits for readiness (no sleep 2)
    def __exit__(...): ...                    # teardown, always
```

- The stdlib CONNECT proxy (`scripts/shakedown/assets/harbor/proxy.py`) is
  promoted to a maintained artifact whose `ALLOW` set is **injected** from
  the resolved allowlist — fixing the current three-way manual sync between
  `run.config.yaml`, the proxy's hardcoded set (`proxy.py:19`), and
  `squid.conf:20`. Its JSONL contract (`{trial,host,decision[,cost]}`) and
  trial-id-as-userinfo auth are frozen (external deployments +
  `test_e2e_metering_proxy.py` pin them).
- Placement note: `hermetic` names neither `harbor` nor any engine — the
  AST seam sweep (`tests/test_eval4_seam.py:88-120`) and the import-linter
  harbor contract stay green; add the package to the enumerated source
  lists (A5 mechanics).
- Surfacing: `MeteringProxy.managed(...)` via SDK, `bench proxy up/down`
  for operators, and an opt-in `proxy.managed: true` in `run.config.yaml`.
- `deploy/metering-proxy/` remains the external-production reference
  (Squid), with its documented credential caveat
  (`docs/design/shakedown.md:109-115`).

This single item deletes the 7-step × 2-script hand lifecycle and the
"must match harbor's constant" comment (`harbor_multiagent.py:28-30`).

## 2. Engine ABC + registry (Phase 4)

Today `Engine` is a 2-method Protocol (`harness/run/types.py:108-111`)
whose real contract is folklore: write `artifacts/agent_log.json`, resolve
digests, confirmed kill-on-timeout, the closed `failure_reason` vocabulary
(including the exact string `"proxy_log_missing"` that aborts the run,
`interleave.py:75, 391-395`), keep in-memory `native_log` consistent with
on-disk bytes. A new engine would be written by reverse-engineering
harbor.py (~5 files touched, most of them lists that hardcode
`fake | harbor`).

Target:

```python
# harness/run/engines/base.py
class EngineBase(ABC):
    name: ClassVar[str]
    def run(self, req: TrialRequest) -> EngineResult:      # final template:
        image = self._resolve_image(req)                   # digest pin or refuse
        exec_ = self._execute(req, image)                  # subclass-owned
        native = self._read_native_log(req)                # shared, fail-closed RN-17
        egress = self._scan_proxy_log(req)                 # shared, fail-closed PRA-H4
        return self._assemble(exec_, native, egress)       # shared precedence ladder
    @abstractmethod
    def _execute(self, req, image) -> ExecOutcome: ...
```

- `_read_native_log` / `_scan_proxy_log` are hoisted out of harbor
  (`harbor.py:360-380, 402-487`) so every engine gets identical
  fail-closed semantics. **A10:** this gives the fake engine
  `proxy_log_missing` parity *when a proxy is configured* (today it
  silently ignores an unwritable log, `fake.py:52-65`, and scheduler tests
  script the reason string by hand, `tests/test_eval4_cost.py:311`).
  Behavior change is confined to configured-proxy fakes; tests updated
  deliberately, listed in the PR.
- The outcome-downgrade precedence (kill_failed > daemon_error > timeout >
  telemetry_corrupt > proxy_log_missing, `harbor.py:339-380`) moves intact —
  it is pinned by the eval4 lifecycle/egress/cost suites.
- Registry: `ENGINES: dict[str, Callable[[], EngineBase]]` replaces the
  if/elif factory (`engines/__init__.py:14-22`); the CLI help and error
  text derive from `ENGINES.keys()`; the cross-engine contract suite
  (`tests/test_eval4_seam.py:44-69`) parametrizes over the registry so a
  new engine is automatically contract-tested.
- `docs/engines.md` states the obligations table (from the audit) as the
  normative engine contract.
- Harbor confinement is untouched: `EngineBase` lives beside the factory;
  only `engines/__init__.py` and `harbor.py` name harbor.

## 3. `run_trial` capture pipeline (Phase 5)

`run_trial` is a 230-line, 7-phase function (`harness/run/seam.py:72-302`)
whose spend-carry works by **mutating exceptions** (`exc.enforcement_cost`
attached in four separate wrappers, consumed via `getattr` in
`interleave.py:293`) — a hidden cross-module contract invisible to types.
Trajectory and flight-recorder capture are two near-identical inline blocks
(`seam.py:204-246`) backed by near-clone persistence modules
(`trajectory.py:148-187` vs `flight_recorder.py:174-211`).

Target:

- `SpendTracker` threaded explicitly; post-engine failures raise a typed
  `PostEngineFailure(reason, spend)`; `interleave` consumes the type, not
  an attribute.
- `CaptureStage` protocol (redact → trajectory → flight recorder), one
  shared wrapper enforcing the ordering constraint (capture only after
  redaction), the timeout-truncation carve-out (`seam.py:209-217`), and the
  infra-failed skip in one place. A third artifact type becomes one new
  stage, not a third copy of two blocks plus a `TrialRecord` edit in four
  files.
- `persist_versioned_artifact` merges the twin scrub→revalidate→write→
  readback persistence paths; canonical-bytes recipes per artifact are
  frozen (`trajectory_sha`/`flight_recorder_sha` ride the chain).
- The dual-source invariant (telemetry from in-memory `native_log`,
  trajectory from redacted on-disk bytes — deliberate, `seam.py:171,208`)
  gets stated in `docs/engines.md` and asserted by a contract test instead
  of living in comments.

Constraining tests: PRA-M8 spend-carry suite, trajectory/flight-recorder
corrupt-fails-closed suites, `TrialRecord` shape stability test.

## 4. `RunConfigFile` model (Phase 1, small)

One pydantic reader for `run.config.yaml` (proxy, quotas,
`provider_key_names[_by_arm]`, reuse_control) replacing the isinstance
ladder (`settings.py:86-161`) and the CLI's second raw read of the same
file (`cli.py:143-148`); exact refusal strings preserved (several tests pin
them). The `Quotas(cpus=2.0, mem="4g")` default collapses from three sites
(`types.py:118`, `settings.py:47`, `settings.py:128`) to one constant.
Builder-side emission in [02](02-experiment-sdk.md).

## 5. Scheduler decomposition (optional, late Phase 5)

`schedule` mixes resume, quarantine preflight, cost guard, heartbeat, fault
taxonomy, and ledger appends, threading control flow through a mutated
result object (`interleave.py:199-329`; ceiling-stop flag set at
`:139-146`, re-detected at `:299-300`). Split into a `TrialPlanner`
(resume/skip/ordering — pure) and a `TrialExecutor` (cost guard, reruns,
events) only if Phase-5 capacity allows; the `executed_order` event,
one-event property, and resume semantics (`_prior_run_state`,
`interleave.py:100-136`) are hard pins. Also move the import-time
entrypoint fixture blocks per [06](06-ledger-telemetry.md) §6.

## 6. Invariants

`TrialRecord` shape + telemetry-nulls mirror + sha hoisting; generic log
v1/v2 frozen; trajectory v3 + flight-recorder v3 canonical bytes + closed
role vocabulary; `/verdi/request.json` path and existing keys (extension is
A1); proxy JSONL + userinfo auth; `failure_reason` closed vocabulary;
hermetic run flags; `METERED_NETWORK` name; ledger event kinds emitted by
run; control fingerprint + bundle versions (changes only via their version
levers); heartbeat schema v1; workspace walk v1; harbor confinement
contract + AST sweep.
