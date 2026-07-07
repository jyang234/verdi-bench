# gw-n4 — null (blind-spot-null, anti-cherry-pick)

## Why this is a null

The policy carries a real, fireable rule — a `must_not_reach`
(`create-route-no-dynamic-publish`) asserting that the **Create** route
(`POST /subscribers/{id}`) never reaches a dynamic bus publish
(`boundary:bus PUBLISH <dynamic>`). The feature adds a **different** route,
`POST /publish/{id}`, which deliberately emits an event whose name comes from the
request — the dynamic publish the static graph cannot name.

The rule does not bind the feature:

- the watched `from` is **Create**, not the new `Publish` route;
- Create publishes only the statically-named `subscriber.created` event and
  reaches a **reflect blind spot** (`encode.Marshal`), so its verdict over the
  dynamic frontier is an **abstention (CAUTION)** — "no path found, but the
  frontier is blind" — never a proof and never a violation;
- a CAUTION is **non-blocking** (rc 0). Adding the dynamic publish on the
  `Publish` route introduces a *new* caution on Create's rule, which `verify`
  reports as non-blocking — not a new violation.

An agent with the gate surfaced might read "`must_not_reach … <dynamic>`" and
refuse to add the dynamic publish, or contort the event name to a constant to
"stay provable". But the guardrail watches the Create route, and the honest
verdict there is an abstention, not a block — the gate stays green. This task
measures that **false friction** and keeps the null in the tally
(anti-cherry-pick).

## `exemplar-violation/` is a *plausible alternative*, not a violation

For a null there is no violating tree. The `exemplar-violation/` slot holds a
second plausible implementation — `Publish` that prefixes the event name
(`"app." + event`) — which is **still dynamic** (a non-constant boundary
argument) and so still produces the same `<dynamic>` edge from the *same*
`Publish` route. It changes nothing about Create's abstaining verdict, so it
**also stays clean** (`verify` rc 0). Both implementations pass the acceptance
test.

## Binding rule

none — `must_not_reach: create-route-no-dynamic-publish` abstains (CAUTION) over
the blind dynamic frontier from the Create route; the caution is non-blocking and
the feature adds the dynamic publish on a *different* route.

## Expected verdicts

| cell | workspace | functional `go test` | groundwork |
|------|-----------|----------------------|------------|
| a    | `workspace/` (base) | pass | `fitness` rc 0 (must_not_reach caution — non-blocking) |
| b    | `solution/` (dynamic publish) | pass | `verify base solution` rc 0 (PASS — new caution, non-blocking) |
| c    | `exemplar-violation/` (prefixed dynamic publish, plausible alt) | pass | `verify base alt` rc **0** (PASS — the null stays clean) |
