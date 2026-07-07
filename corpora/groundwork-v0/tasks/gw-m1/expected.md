# gw-m1 — multi-impl residual (must_not_reach hidden behind interface dispatch)

## Trap (one property under test)

`views.Counter` has three implementations — `MemCounter` (in-process),
`NopCounter` (does nothing), `LedgerCounter` (a DB write per `Bump`). Which one a
`Bump` call actually reaches is decided **only by what `main` wires**. The base
already wires `LedgerCounter` for signups (a legitimate write route).

The feature bumps a counter on `GET /accounts/{id}`. The **tempting**
implementation reuses the ledger-backed counter for views too — so the read
route reaches `repo.SQLStore.IncrCounter` (a `db UPDATE`). The **clean**
implementation wires an in-memory `MemCounter` for views, keeping the read route
read-only. `core.Service.GetAccount`, `wire.Handler`, and the feature test are
**byte-identical** between the two trees; only the counter wired in `main.go`
differs.

## Why this is the postmortem's residual

A hand-tracer must enumerate all three `Counter` implementations and infer which
is wired to know whether the read route writes. This is the one place a
capability edge might survive:

- Under **RTA** the gate itself cannot tell them apart — both `MemCounter` and
  `LedgerCounter` are instantiated, so `verify` **false-positives the clean
  solution** (rc 1).
- Under **VTA** (this task's substrate) the gate resolves the `views` field to
  the concrete value wired in — clean solution **passes** (rc 0), tempting
  exemplar **blocks** (rc 1).

The task therefore pins `"substrate": "vta"` in the policy; base and branch
graphs must both be built `flowmap graph --algo vta`.

## Binding rule

`must_not_reach: read-route-stays-read-only` — `api.Server.GetAccount` must not
reach a `db` write. The exemplar reaches `db UPDATE counters` via
`LedgerCounter.Bump`.

## Expected verdicts (VTA substrate)

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS under VTA; would false-BLOCK under RTA) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **must_not_reach** |
