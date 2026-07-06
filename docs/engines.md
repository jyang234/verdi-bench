# Engines and the run contract

The single **normative** statement of what a verdi *engine* must obey [refactor
04 §2]. An engine turns a `TrialRequest` into an `EngineResult`: it runs the agent
(in a hermetic container, or a scripted fixture) and reports the outcome, telemetry,
egress, and provenance the seam normalizes into a `TrialRecord`. The code seam is
`harness/run/engines/base.py` (`EngineBase`); the two shipped engines are `harbor`
(hermetic Docker) and `fake` (the deterministic fixture backbone). This is the
engine-side companion to `docs/images.md` §1 — that document is the trial *image*
contract (what runs inside the container); this one is the *engine* contract (what
the harness around it must guarantee).

Before `EngineBase` the contract was folklore reverse-engineered from Harbor. It is
now a template method: `EngineBase.run` is `final` and a subclass fills only two
seams — `_resolve_image` and `_execute` — inheriting every obligation below. So a
new engine is contract-correct by construction, and the cross-engine contract suite
(`tests/test_eval4_seam.py`) proves it against every registered engine.

## The engine contract (§1)

Every engine must obey the following. The right column names where each obligation
is inherited from `EngineBase` or enforced — not a second contract to keep in sync.

| Obligation | Inherited / enforced by |
|---|---|
| Stage the task's declared `files` into `/workspace` and create `<workspace>/artifacts/` before the agent runs | `EngineBase._prepare_workspace` (shared); `stage_files` refuses paths that escape the workspace [A3, PRA-M5] |
| Resolve the image to an **immutable** digest-pinned ref, or refuse — a tag-only/unresolvable image fails the trial closed `infra_failed(unpinned_image)`, never runs unpinned | `_resolve_image -> ResolvedImage` (subclass); Harbor via `resolve_pinned` [D005, RN-12, F-M-I2] |
| Record the resolved `image_digest` in provenance (a `sha256:…` content address) | `EngineBase._assemble` reads `ResolvedImage.digest`; asserted by the contract suite |
| Write telemetry to `<workspace>/artifacts/agent_log.json`; a **present but corrupt** log fails the trial closed `infra_failed(telemetry_corrupt)`, never silently "no telemetry" | shared `_read_native_log` raises `TelemetryCorruptError` [RN-17] |
| Keep the in-memory `native_log` (the telemetry source) consistent with the on-disk **pre-redaction** bytes (the trajectory source) — the dual-source invariant (§3) | shared `_read_native_log`; the fake supplies `ExecOutcome.native_log` explicitly; asserted by the contract suite |
| Route egress ONLY through the metering proxy and report per-trial attempts/violations; a **configured but absent** proxy log fails the trial closed `infra_failed(proxy_log_missing)`, never a silent zero (§2) | shared `_scan_proxy_log` raises `ProxyLogMissingError` [PRA-H4] |
| On timeout, **confirm** the container is killed/reaped before the workspace is redacted; an unconfirmed kill fails the trial closed `infra_failed(kill_failed)` | Harbor `DockerCliRunner._kill` (`docker inspect`, not the `--rm` exit codes) [RN-10, PRA-M7] |
| Stamp only closed-vocabulary `failure_reason` values (§2), so the scheduler ledgers a real reason, never a fixture placeholder | `_execute` + the shared downgrades; `ENGINE_FAILURE_REASONS` [RN-14] |
| Inject provider keys as env at trial start — never on the argv, in image layers, or the ledger | Harbor `build_run_command` passes key NAMES; values reach docker via the child env [AC-8] |

`EngineResult`, `Provenance`, and the `TrialRecord` shapes are versioned public
seams — changing them is a contract change, not an engine detail.

## The outcome precedence and the `failure_reason` vocabulary (§2)

`EngineBase.run` assembles the outcome with one fixed downgrade precedence:

```
kill_failed > daemon_error > timeout > telemetry_corrupt > proxy_log_missing
```

The head (`kill_failed > daemon_error > timeout`) is determined by the engine inside
`_execute` — for Harbor from the container result, for the fake from its script. The
tail (`telemetry_corrupt`, then `proxy_log_missing`) is applied by the shared
`_assemble`, each **only against a would-be-`completed` trial**, so a more specific
engine reason is never masked and telemetry corruption outranks a missing proxy log.

`failure_reason` is a **closed vocabulary** (`ENGINE_FAILURE_REASONS` in `base.py`):

| `failure_reason` | Meaning | Outcome |
|---|---|---|
| `unpinned_image` | the image could not be resolved to an immutable digest | `infra_failed` (before any container runs) |
| `kill_failed` | a timed-out container's kill/reap could not be confirmed | `infra_failed` |
| `daemon_error` | the container runtime failed before the agent ran (docker exit 125) | `infra_failed` |
| `telemetry_corrupt` | `agent_log.json` was present but not valid JSON | `infra_failed` (downgrade of `completed`) |
| `proxy_log_missing` | a configured metering-proxy log file was absent | `infra_failed` (downgrade of `completed`) |

`proxy_log_missing` carries a **run-abort coupling**: it is a run-level fault (a dead
or misconfigured proxy fails every remaining trial identically), so the scheduler
aborts the whole run after ledgering the first occurrence rather than grinding
through the schedule — the exact string is matched in
`harness/run/interleave.py` (`PROXY_UNAVAILABLE_REASON` → `ProxyUnavailableError`)
[PRA-M9]. Changing the string silently unhooks that abort.

The fake engine may additionally script an **arbitrary** `infra_reason` placeholder
via `fake_behavior` — a fixture affordance for driving downstream stories, not part
of the closed contract. Since the fake inherits the shared `_scan_proxy_log`, it now
reaches `proxy_log_missing` **organically** when a proxy is configured whose log
never appears (A10 parity) — the same fail-closed path Harbor takes — instead of
scripting the reason string by hand.

## The dual-source telemetry/trajectory invariant (§3)

Telemetry and the trajectory are read from **two deliberately different sources**
(`harness/run/seam.py`):

- **Telemetry** is normalized from the engine's in-memory `native_log` — the
  **pre-redaction** log the engine read (Harbor) or scripted (the fake).
- **Trajectory** is captured from the **post-redaction** on-disk `agent_log.json`,
  re-read after `redact_artifacts` scrubs the workspace, so a transcript can never
  leak a secret the telemetry aggregate would not.

The invariant the engine guarantees: its in-memory `native_log` equals the on-disk
**pre-redaction** bytes. Harbor satisfies this by construction (the disk is its only
source); the fake satisfies it by writing exactly what it returns, and carries the
in-memory copy on `ExecOutcome.native_log` so a fixture that corrupts the on-disk
log (to exercise the seam's trajectory fail-closed path) does not also corrupt the
telemetry read. The contract suite asserts in-memory `native_log == on-disk bytes`
for every engine.

## Adding an engine (§4)

Add one entry to `ENGINES` in `harness/run/engines/__init__.py` (`name -> zero-arg
factory`). That single line wires everything: `get_engine` resolves it, the `bench
run --engine` help and the unknown-engine error derive their choice lists from
`ENGINES.keys()`, and the cross-engine contract suite parametrizes over the registry
so the new engine is contract-tested automatically. Implement `_resolve_image` and
`_execute` on an `EngineBase` subclass; everything in §1–§3 is inherited. Only
`harness/run/engines/harbor.py` and `engines/__init__.py` may name Harbor — the
import-linter `harbor-confined-to-seam` contract and the AST seam sweep keep the
confinement structural [AC-1].
