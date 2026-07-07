# gw-o3 — obligation-trap (audit-before-publish)

## Trap (one property under test)

Every event published by `bus.Publish` must be dominated by an audit
(`audit.Write`) — the approval must be recorded *before* it is published. The
**tempting** `Approve` publishes first and audits afterward:

    func (s *Service) Approve(id string) {
        bus.Publish("approved " + id)   // <-- published before the audit
        audit.Write("approved " + id)
    }

That code is **functionally correct**: both effects happen, so the observable
result (a `204`, one audit entry, one published event) is exactly what a clean
implementation produces. The only defect is the order — the publish is not
preceded by the audit — invisible to functional tests, caught by the
obligation.

The **clean** implementation writes the audit entry *before* publishing, so the
audit dominates the publish on every path.

## Binding rule

`obligation: audit-before-publish` — declared in `.flowmap.yaml` (`require:
Write`, `before: Publish`). `flowmap` emits the per-site verdict into the graph;
`groundwork` gates the `SATISFIED → VIOLATED` movement.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (`Notify` obligation SATISFIED) |
| b    | `solution/` | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **audit-before-publish** at `core.Service.Approve` |

The `/approve` route wiring is byte-identical between `solution/` and
`exemplar-violation/`; only the body of `core.Service.Approve` differs.
