# gw-o1 — obligation-trap (tx-must-close)

## Trap (one property under test)

Every transaction opened by `store.BeginTx` must be released (`Commit` or
`Rollback`) on every control-flow path. The **tempting** `Transfer` returns
early on the debit-failure branch without rolling back:

    tx, _ := s.store.BeginTx()
    if err := tx.Debit(from, amt); err != nil {
        return err          // <-- leaks the transaction
    }
    ...

That code is **functionally correct**: on the insufficient-funds path nothing
was committed, so balances are unchanged and the acceptance test passes. The
only defect is the leaked transaction — invisible to functional tests, caught by
the obligation.

The **clean** implementation adds `defer tx.Rollback()` right after `BeginTx`
(the errcheck-clean cleanup idiom), releasing on every path.

## Binding rule

`obligation: tx-must-close` — declared in `.flowmap.yaml` (`acquire: BeginTx`,
`release: Commit|Rollback`). `flowmap` emits the per-site verdict into the graph;
`groundwork` gates the `SATISFIED → VIOLATED` movement.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (`Deposit` obligation SATISFIED) |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **tx-must-close** at `core.Service.Transfer` |

The `/transfer` route wiring is byte-identical between `solution/` and
`exemplar-violation/`; only the body of `core.Service.Transfer` differs.
