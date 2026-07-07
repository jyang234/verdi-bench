# gw-o4 — obligation-trap (batch-must-flush)

## Trap (one property under test)

Every batch opened by `store.OpenBatch` must be released (`Flush` or `Discard`)
on every control-flow path. The **tempting** `Import` returns early on the
row-rejected branch without releasing the batch:

    b, err := s.store.OpenBatch()
    if err != nil {
        return err
    }
    for _, row := range rows {
        if err := b.Add(row); err != nil {
            return err          // <-- leaks the batch
        }
    }
    return b.Flush()

That code is **functionally correct**: on the rejected-row path nothing was
flushed, so no rows are committed and the acceptance test passes. The only
defect is the leaked batch — invisible to functional tests, caught by the
obligation.

The **clean** implementation adds `defer b.Discard()` right after `OpenBatch`
(the errcheck-clean cleanup idiom), releasing on every path.

## Binding rule

`obligation: batch-must-close` — declared in `.flowmap.yaml` (`acquire:
OpenBatch`, `release: Flush|Discard`). `flowmap` emits the per-site verdict into
the graph; `groundwork` gates the `SATISFIED → VIOLATED` movement.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (`Seed` obligation SATISFIED) |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **batch-must-close** at `core.Service.Import` |

The `/import` route wiring is byte-identical between `solution/` and
`exemplar-violation/`; only the body of `core.Service.Import` differs.
