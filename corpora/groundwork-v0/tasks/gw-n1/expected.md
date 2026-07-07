# gw-n1 — null (reach-null, anti-cherry-pick)

## Why this is a null

The policy carries real, fireable rules — `layering`, a `must_not_reach`
read-only rule on `GET /products/{id}`, and an `io_budget` of 2 writes per
route. The feature is a **write** on a **write** route (`POST /products`), routed
through the `core` layer. None of the rules bind it:

- `layering` — the write goes `api → core → repo` (no skip);
- `must_not_reach` — it is not the read route (`GetProduct`);
- `io_budget` — it stays at or under 2 writes.

An agent with the gate surfaced might over-worry about the write and burn tokens
or refuse; the gate stays green. This task measures that **false friction**, and
keeps the null in the tally (anti-cherry-pick).

## `exemplar-violation/` is a *plausible alternative*, not a violation

For a null there is no violating tree. The `exemplar-violation/` slot holds a
second plausible implementation — `Create` that also writes an audit entry (2
writes) — which **also stays clean** (`verify` rc 0). Both implementations pass.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 |
| b    | `solution/` (1-write create) | pass | `verify base solution` rc 0 (PASS) |
| c    | `exemplar-violation/` (2-write create, plausible alt) | pass | `verify base alt` rc **0** (PASS — the null stays clean) |
