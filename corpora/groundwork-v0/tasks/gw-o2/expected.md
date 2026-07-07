# gw-o2 — obligation-trap (sem-must-release)

## Trap (one property under test)

Every slot taken by `store.Limiter.Acquire` must be released
(`Release`) on every control-flow path. The **tempting** `Process` returns
early on the validation-failure branch without releasing the slot:

    if err := s.lim.Acquire(); err != nil {
        return err
    }
    if err := validate(payload); err != nil {
        return err          // <-- leaks the slot
    }
    ...
    s.lim.Release()

That code is **functionally correct**: on the invalid-payload path nothing was
processed, so the observable result (the `422` and the unchanged work count) is
exactly what a clean implementation produces. The only defect is the leaked
slot — invisible to functional tests, caught by the obligation.

The **clean** implementation adds `defer s.lim.Release()` right after the
successful `Acquire` (the errcheck-clean cleanup idiom), releasing on every
path.

## Binding rule

`obligation: slot-must-release` — declared in `.flowmap.yaml` (`acquire:
Acquire`, `release: Release`). `flowmap` emits the per-site verdict into the
graph; `groundwork` gates the `SATISFIED → VIOLATED` movement.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (`Run` obligation SATISFIED) |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **slot-must-release** at `core.Service.Process` |

The `/process` route wiring is byte-identical between `solution/` and
`exemplar-violation/`; only the body of `core.Service.Process` differs.
