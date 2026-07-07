# gw-m3 — multi-impl residual (must_not_reach hidden behind interface dispatch)

## Trap (one property under test)

`emit.Emitter` has three implementations — `LogEmitter` (writes the process
log), `NopEmitter` (does nothing), and `BusEmitter` (a `bus PUBLISH` per `Emit`).
Which one an `Emit` call actually reaches is decided **only by what `main`
wires**. The base already wires `BusEmitter` for reactions on
`POST /feed/{id}/react` (a legitimate write route).

The feature emits an activity event on `GET /feed/{id}`. The **tempting**
implementation reuses the bus-backed emitter for read activity too — so the read
route reaches `bus.Bus.Publish` (a `bus PUBLISH`) through
`core.Service.recordActivity`. The **clean** implementation wires the log-backed
`LogEmitter` for read activity, keeping the read route off the bus.
`core.Service.GetItem`, `wire.Handler`, and the feature test are
**byte-identical** between the two trees; only the emitter wired in `main.go`
differs.

## Why this is the postmortem's residual

A hand-tracer must enumerate all three `Emitter` implementations and infer which
is wired to know whether the read route publishes. This is the one place a
capability edge might survive:

- Under **RTA** the gate itself cannot tell them apart — both `LogEmitter` and
  `BusEmitter` are instantiated (one for reads, one for the reaction route), so
  `verify` **false-positives the clean solution** (rc 1).
- Under **VTA** (this task's substrate) the gate resolves the `activity` field
  to the concrete value wired in — clean solution **passes** (rc 0), tempting
  exemplar **blocks** (rc 1).

The task therefore pins `"substrate": "vta"` in the policy; base and branch
graphs must both be built `flowmap graph --algo vta`.

## Binding rule

`must_not_reach: read-route-stays-read-only` — `api.Server.GetItem` must not
reach a `bus PUBLISH`. The exemplar reaches `bus PUBLISH feed.activity` via
`recordActivity → BusEmitter.Emit → bus.Bus.Publish`.

## Expected verdicts (VTA substrate)

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS under VTA; would false-BLOCK under RTA) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **must_not_reach** |
