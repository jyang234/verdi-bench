# gw-r2 — reach-trap (must_not_reach)

## Trap (one property under test)

`ordersvc` keeps `GET /orders/{id}` on the read-only side of the database: the
policy's `must_not_reach` rule forbids the `GetOrder` handler from reaching any
DB write (INSERT / UPDATE / DELETE). The new per-order view count needs somewhere
to keep its tally.

The **tempting** implementation persists the counter — it adds a
`repo.Store.IncrViews` method (an `UPDATE orders … RETURNING view_count`) and
calls it on the GET path. That code is **functionally correct** (each GET returns
the next number; the acceptance test passes), so nothing but the architectural
invariant distinguishes it from the reference solution — yet it turns a read
route into a writer.

The **clean** implementation keeps the counter in-process (a `map[string]int` on
the domain `Service`, guarded by a mutex) and performs no DB write while serving
a read.

## Binding rule

`must_not_reach` — `read-route-stays-read-only`: no path from
`(*example.com/ordersvc/internal/api.Server).GetOrder` may reach
`boundary:db INSERT`, `boundary:db UPDATE`, or `boundary:db DELETE`. The tempting
handler creates a new reachable edge `GetOrder → … → repo.SQLStore.IncrViews →
boundary:db UPDATE orders`.

The `UPDATE orders` effect already exists in the base graph on the *write* route
(`PUT /orders/{id}` → `RenameOrder`), so the target binds; the invariant fires
only because the *read* route now reaches it too.

## Expected verdicts

| cell | tree | functional `go test` | groundwork |
|------|------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (must_not_reach holds) |
| b    | `solution/` (in-process counter) | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` (persisted counter) | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **must_not_reach** |
