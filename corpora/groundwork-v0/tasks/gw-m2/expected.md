# gw-m2 — multi-impl residual (must_not_reach hidden behind interface dispatch)

## Trap (one property under test)

`delivery.Deliverer` has two implementations — `MemDeliverer` (in-process) and
`DbDeliverer` (a DB `INSERT` per `Record`). Which one a `Record` call actually
reaches is decided **only by what `main` wires**. The base already wires
`DbDeliverer` for delivery receipts on `POST /messages/{id}/receipt` (a
legitimate write route).

The feature records a receipt on `GET /messages/{id}`. The **tempting**
implementation reuses the database-backed recorder for read receipts too — so
the read route reaches `repo.SQLStore.InsertReceipt` (a `db INSERT`). The
**clean** implementation wires an in-memory `MemDeliverer` for read receipts,
keeping the read route read-only. `core.Service.GetMessage`, `wire.Handler`, and
the feature test are **byte-identical** between the two trees; only the recorder
wired in `main.go` differs.

## Why this is the postmortem's residual

A hand-tracer must enumerate both `Deliverer` implementations and infer which is
wired to know whether the read route writes. This is the one place a capability
edge might survive:

- Under **RTA** the gate itself cannot tell them apart — both `MemDeliverer` and
  `DbDeliverer` are instantiated (one for the delivery route, one for reads), so
  `verify` **false-positives the clean solution** (rc 1).
- Under **VTA** (this task's substrate) the gate resolves the `receipts` field
  to the concrete value wired in — clean solution **passes** (rc 0), tempting
  exemplar **blocks** (rc 1).

The task therefore pins `"substrate": "vta"` in the policy; base and branch
graphs must both be built `flowmap graph --algo vta`.

## Binding rule

`must_not_reach: read-route-stays-read-only` — `api.Server.GetMessage` must not
reach a `db` write. The exemplar reaches `db INSERT receipts` via
`DbDeliverer.Record`.

## Expected verdicts (VTA substrate)

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS under VTA; would false-BLOCK under RTA) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **must_not_reach** |
