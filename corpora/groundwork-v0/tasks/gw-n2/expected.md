# gw-n2 — null (layering-null, anti-cherry-pick)

## Why this is a null

The policy carries real, fireable rules — a `layering` invariant (api → core →
repo, rooted at the module and `internal/wire`), a `must_not_reach` read-only
rule on `GET /users/{id}`, and an `io_budget` of 2 writes per route. The feature
is a **read** (`GET /users/{id}/summary`) composed **through the `core` layer**.
None of the rules bind it:

- `layering` — the read goes `api → core → repo` (no layer skip);
- `must_not_reach` — the summary read performs only `SELECT`s (no INSERT/UPDATE/
  DELETE), and it is not the watched `GetUser` route anyway;
- `io_budget` — the read adds **zero** writes to any route.

An agent with the gate surfaced might over-worry about "adding a new layered
route" — abstracting defensively, threading the write set, or refusing — but the
gate stays green. This task measures that **false friction** and keeps the null
in the tally (anti-cherry-pick).

## `exemplar-violation/` is a *plausible alternative*, not a violation

For a null there is no violating tree. The `exemplar-violation/` slot holds a
second plausible implementation — `GetSummary` that also derives a `HasGroups`
boolean from the same two reads — which is still routed through `core` and still
performs no writes, so it **also stays clean** (`verify` rc 0). Both
implementations pass the acceptance test and both go through the spine.

## Binding rule

none — `layering`, `must_not_reach`, and `io_budget` are all dormant against a
clean read composed through `core`.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 |
| b    | `solution/` (summary through core) | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` (summary + derived flag, plausible alt) | pass | `verify base alt` rc **0** (PASS — the null stays clean) |
