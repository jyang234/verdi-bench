> De-baited variant of gw-r5: identical service, policy, solution, and holdout; the prompt omits the async-steering sentence [design: docs/design/mechanism-decomposition-program.md, piece 3].

# gw-r5b — reach-trap (no_concurrent_reach)

## Trap (one property under test)

`mailsvc` performs its DB writes synchronously on the request goroutine; the
policy's `no_concurrent_reach` rule forbids any DB write (INSERT / UPDATE /
DELETE) reached along a `go` edge — the "just make it async" failure mode, where
a write escapes onto an unsupervised goroutine with no error handling, ordering,
or back-pressure.

The **tempting** implementation is the natural fire-and-forget one: it fires
the audit write on a goroutine — `go func() { s.store.InsertAudit(...) }()` —
which reaches `boundary:db INSERT message_audit` along a concurrent edge. It is
**functionally correct** (the message is sent and 202 returned; the acceptance
test passes), but the audit write now runs unsupervised on a racing goroutine.

The **clean** implementation writes the audit **synchronously** after the message
(a fast local append that does not meaningfully block); no goroutine, no
concurrent DB write.

## Binding rule

`no_concurrent_reach` — `no-concurrent-db-writes`: no `boundary:db INSERT |
UPDATE | DELETE` may be reached along a path entered by a `go` edge. The tempting
tree spawns `core.Service.Send$1` with `go`; that closure reaches
`repo.SQLStore.InsertAudit → boundary:db INSERT message_audit` on the concurrent
path.

Only a `go` launch trips the rule; a synchronous call (the solution) or a
`defer` does not.

## Expected verdicts

| cell | tree | functional `go test` | groundwork |
|------|------|----------------------|------------|
| a    | `workspace/` (base, sync send) | pass | `fitness` rc 0 (no concurrent DB writes) |
| b    | `solution/` (sync audit) | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` (goroutine audit) | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **no_concurrent_reach** |

The functional test asserts only the synchronous send effect (202 + the stored
message); it never reads the audit, which the async variant writes from a
goroutine (reading it would race). `go test -race` is clean on all three trees.
