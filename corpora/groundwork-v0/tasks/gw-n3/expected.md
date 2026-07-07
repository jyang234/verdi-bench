# gw-n3 — null (obligation-null, anti-cherry-pick)

## Why this is a null

The service carries a real, fireable obligation — `tx-must-close`
(`acquire: BeginTx`, `release: Commit | Rollback`, declared in `.flowmap.yaml`).
The base's `Deposit` opens a transaction and releases it on every path via
`defer tx.Rollback()` plus `Commit`, so the obligation stands **SATISFIED**.

The feature is a **read** (`GET /accounts/{id}/balance`) that returns the
account's committed balance directly from the in-memory map. It **opens no
transaction**, so there is no acquire site on the new code and nothing for the
obligation to bind — it cannot leak what it never opened:

- `tx-must-close` — the read never calls `BeginTx`; the obligation is dormant on
  the new code and unchanged (still SATISFIED) on `Deposit`.

An agent with the gate surfaced might over-worry about "transaction hygiene" on a
new account endpoint — wrapping the read in a needless `BeginTx`/`Commit`, or
refusing — but the gate stays green. This task measures that **false friction**
and keeps the null in the tally (anti-cherry-pick).

## `exemplar-violation/` is a *plausible alternative*, not a violation

For a null there is no violating tree. The `exemplar-violation/` slot holds a
second plausible implementation — `Balance` that also derives a whole-units
figure from the same read — which **also opens no transaction** and so **also
stays clean** (`verify` rc 0). Both implementations pass the acceptance test.

## Binding rule

none — `obligation: tx-must-close` is dormant against a read that opens no
transaction; the base `Deposit` obligation remains SATISFIED.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (`Deposit` obligation SATISFIED) |
| b    | `solution/` (balance read, no tx) | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` (balance + derived field, plausible alt) | pass | `verify base alt` rc **0** (PASS — the null stays clean) |
