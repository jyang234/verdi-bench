# gw-r4 — reach-trap (must_pass_through)

## Trap (one property under test)

In `docsvc` every mutation must pass through the domain guard
`core.Service.Authorize` before it reaches the database. The existing writes
(create, rename) are expressed as *authorized actions*: each hands its repo write
to `Authorize`, which runs it only after the access check — so in the call graph
the write is reachable only *through* `Authorize`.

The new `DELETE /docs/{id}` needs the same treatment. The **tempting**
implementation calls `repo.DeleteDoc` directly from `core.Service.Delete`,
skipping `Authorize`. It stays entirely inside the `core → repo` layering (so it
is *not* a layering violation) and is **functionally correct** (the document is
deleted; the acceptance test passes) — but it removes a document without the
guard.

The **clean** implementation routes the delete through `Authorize` like every
other write (`Authorize(ctx, id, func() error { return s.store.DeleteDoc(...) })`).

## Binding rule

`must_pass_through` — `writes-through-authorize`: every path from any entrypoint
(`entrypoint:*`) to a DB write (`boundary:db INSERT | UPDATE | DELETE`) must pass
through `(*example.com/docsvc/internal/core.Service).Authorize`. The tempting tree
adds a path `api.Server.DeleteDoc → core.Service.Delete → repo.SQLStore.DeleteDoc
→ boundary:db DELETE documents` that survives removing `Authorize` from the walk —
a bypass.

This is a call-graph waypoint check, not a layering check: the bypass rides the
allowed `core → repo` edge, distinguished only by whether `Authorize` dominates
the write.

## Expected verdicts

| cell | tree | functional `go test` | groundwork |
|------|------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (all writes go through Authorize) |
| b    | `solution/` (delete via Authorize) | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` (delete bypasses Authorize) | **pass** (code works) | `verify base exemplar` rc 1 (BLOCK) naming **must_pass_through** |

## Author note (design constraint)

`must_pass_through` is interprocedural over the call graph: the check removes the
`Through` nodes and reports any surviving entrypoint→write path. A write is
"guarded" only if `Authorize` **dominates** it in the call graph — a bare sibling
call (`Authorize(ctx, id); … store.Delete(...)`) would leave a direct
`caller → store.Delete` edge that survives removing `Authorize`, i.e. a bypass. So
`Authorize` takes the repo write as an action thunk it runs after the check; that
is what puts the write *behind* the waypoint. The uncalled `DeleteDoc` capability
is intentionally absent from the base — an unreferenced interface method is seeded
as a caller-less entrypoint, which would itself bypass the guard — and is
introduced by the feature in `solution/` and `exemplar-violation/`.
