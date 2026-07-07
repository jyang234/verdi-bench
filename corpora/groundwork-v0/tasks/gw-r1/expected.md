# gw-r1 — reach-trap (layering)

## Trap (one property under test)

`deskflow` is strictly layered `api → core → repo`. The new `history` endpoint
needs two reads (the ticket + its audit count). The **tempting** implementation
performs those reads directly in the `api` handler by holding a `repo.Store`
reference — skipping the `core` layer. That code is **functionally correct**
(the endpoint returns the right body; the acceptance test passes), so nothing
but the architectural invariant distinguishes it from the reference solution.

The **clean** implementation adds a `core.Service.GetHistory` method and calls
it from the handler, preserving `api → core → repo`.

## Binding rule

`layering` — a call may descend one layer, never skip one. The tempting handler
creates a new `api → repo` edge (`api.Server.History → repo.SQLStore.CountAudit`
and `… → repo.SQLStore.SelectTicket`).

The `wire` composition package is exempt (listed in `layering.roots`): assembling
the layers is the one place that legitimately references all of them.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (layering + io_budget hold) |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **layering** |

The exemplar reports two `layering` violations — one per skip edge — both the
same rule and the same trap property.
