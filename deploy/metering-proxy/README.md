# Reference metering proxy

`bench run --engine harbor` confines trial egress to a **metering proxy** on an
internal docker network and reads that proxy's structured log for per-trial
attribution, egress-violation flagging, and cost enforcement. The proxy is an
**external operational component** — it is not bundled into the harness image
because deployments vary (Squid, a devcontainer proxy, an internal gateway).
This directory is a **reference** implementation of the contract the engine
depends on; validate it in your environment before trusting a real run.

> Since PRA-H4, a configured-but-missing proxy log makes a trial fail
> `infra_failed(proxy_log_missing)` rather than silently reporting zero
> egress/cost — so a dead or misconfigured proxy is loud, not invisible.

## The contract the harness relies on

`harness/run/engines/harbor.py:_scan_proxy_log` parses the proxy log as
**JSON Lines**, one object per request:

```json
{"trial": "<trial-id>", "host": "api.anthropic.com", "decision": "allow"}
{"trial": "<trial-id>", "host": "evil.example.com", "decision": "deny"}
{"trial": "<trial-id>", "host": "api.openai.com", "decision": "allow", "cost": 0.0123}
```

- **`trial`** — the per-trial credential. The harness injects the trial id as
  HTTP-proxy userinfo (`http://<trial-id>@proxy:3128`); the proxy MUST record it
  as the request's `trial` and MUST reject requests bearing an unknown/absent
  credential (otherwise attribution is spoofable — see PRA-H3). Lines whose
  `trial` does not match are ignored by the harness.
- **`host`** — the CONNECT target. Every attempt is logged, allow or deny.
- **`decision`** — `allow` for an allowlisted model-API host, `deny` otherwise.
  Any `deny` for a trial is an egress violation.
- **`cost`** — optional per-request metered cost; summed into the trial's cost so
  a null-telemetry arm is still enforceable against the pre-registered ceiling.

The allowlist is the union of the spec's declared `model_hosts` and
`infra_hosts` (see `harness/run/egress.py`), supplied to the proxy out of band —
never from the sha-locked `experiment.yaml` or the ledger.

## Files

- `squid.conf` — a reference Squid configuration: an allowlist ACL, proxy auth
  carrying the trial id, and a JSON `logformat` matching the schema above.
- `docker-compose.yml` — brings the proxy up on the `verdi-metered` internal
  network the engine attaches trials to.

> **Squid-version caveat.** Harbor presents the trial id as a basic-auth
> *username with an empty password* (`_with_trial_auth`); Squid 6 refuses an
> empty-password credential in core, so validate the auth against your Squid
> version before trusting attribution. The **shipped** in-repo path avoids this
> entirely: the managed metering proxy (`harness/hermetic/_proxy_container.py`,
> stood up and torn down by `MeteringProxy` / `run.config` `proxy.managed`)
> accepts the username-only credential natively and is what the shakedown (L6)
> and the e2e tests use.

## Validation

`tests/test_e2e_metering_proxy.py` (docker-marked, CI only) brings this proxy up,
runs a trial that reaches an allowed host and one that reaches a denied host, and
asserts the log attributes both to the right trial with the right decision.
Because it needs a live Docker daemon + this proxy image, it is deselected by the
fast suite and runs in the dedicated `docker` CI job.
