# gw-r3 — reach-trap (io_budget)

## Trap (one property under test)

`billingsvc` caps each route at **2 distinct write targets** (`io_budget`,
`max_writes_per_route: 2`). A target is a `(table × op)` pair — two INSERTs into
the same table count once; the cap is on the *breadth* of a route's write
surface, not the number of calls.

Finalizing needs to (a) mark the invoice finalized and (b) issue a receipt. The
**tempting** implementation also writes a **separate** `audit_log` row for the
finalize action, giving the route **three** distinct write targets — `UPDATE
invoices` + `INSERT audit_log` + `INSERT receipts` — one over budget. The code is
**functionally correct** (the invoice is finalized and a receipt is issued; the
acceptance test passes); only the write-surface budget separates it from the
reference.

The **clean** implementation folds the finalize audit trail into the invoice's
own columns (`UPDATE invoices SET finalized = …, finalized_by = …`) and issues
the receipt, staying at **two** distinct targets — `UPDATE invoices` + `INSERT
receipts`.

## Binding rule

`io_budget` — `max_writes_per_route: 2`. The finalize route
`(*example.com/billingsvc/internal/api.Server).FinalizeInvoice` reaches 3 distinct
write targets in the tempting tree (`db UPDATE invoices`, `db INSERT audit_log`,
`db INSERT receipts`), exceeding the budget of 2.

The base's write route `POST /invoices` already reaches 2 targets (`INSERT
invoices` + `INSERT audit_log`) and stays at budget; the violation is new and
specific to the finalize route.

## Expected verdicts

| cell | tree | functional `go test` | groundwork |
|------|------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (all routes ≤ 2 targets) |
| b    | `solution/` (2 targets) | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` (3 targets) | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **io_budget** |
