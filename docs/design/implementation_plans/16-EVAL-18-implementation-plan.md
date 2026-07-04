# EVAL-18 implementation plan — reviewer surface

Spec: `docs/design/specs/eval18.spec.md`; decisions D001–D004 resolved in
`eval18.decisions.ndjson` (separate verb + process, launch-bound actor,
native-vocabulary hotkeys, built packet bytes verbatim).

## M1 — the isolated server (AC-1, AC-5)

`harness/review/serve.py:make_review_server` — the reviewer's own process
(D001), sharing the stdlib ThreadingHTTPServer pattern with `bench serve`
but none of its modules: a new strict import-linter contract
(`reviewer-surface-isolated`, indirect chains included) forbids
`harness.review` from reaching `harness.serve`, `harness.status`, or
`harness.author`. The route table is the whole exposure surface: `/`
(queue page), `/api/queue`, `/packet`, and the two capture POSTs — no
status, events, timeline, compare, trial, or artifact route exists to
leak through. `/packet` serves the built `review_packet.html` bytes
verbatim (D004) and re-scans them against `blind.core.arm_canaries`
before they leave the process; a poisoned packet is a 409 with the
reason, never a served page — the belt-and-suspenders scrub the packet
validator precedent demands.

## M2 — capture then reveal over HTTP (AC-2, AC-3)

The two mutating routes wrap the record layer verbatim, adding transport
and nothing else. `POST /api/verdict` validates the winner vocabulary,
requires the integrity answers, translates the reviewer's Response-1/2
choice into the judge's A/B frame via the ledgered `response_map` (the
same recipe as `review record`), and calls `record_human_verdict` — one
POST, one `human_verdict` event, actor = the launch-bound reviewer
(D002, `resolve_actor`, refused loudly at launch). `POST /api/reveal`
calls `reveal_comparison`, which keeps refusing pre-verdict reveals and
double-unblinding beneath the UI. No response ever carries an arm name,
the response_map, or the judge's verdict pre-reveal; refusals surface the
typed error's own message with its class. Everything else: GETs
side-effect-free, non-enumerated POSTs 404, foreign methods 405,
loopback default, no new event kinds.

## M3 — the queue page (AC-4)

`harness/review/serve_page.py:REVIEWER_PAGE` — self-contained (needle
property), carrying the reviewer-tier standing instruction (the inverse
of the operator banner: do not open the operator view). Queue = built
packets minus recorded verdicts, with n-of-m progress; keyboard-first
capture in our native vocabulary (D003): `1`/`2` pick a response, `t`
tie, `c` CANT_JUDGE (always reachable), `j`/`k` navigate, Enter records
and auto-advances — and stays disabled until the two integrity questions
are answered, client- and server-side. The reveal affordance exists only
on recorded comparisons and is its own explicit action.

## M4 — tests

`tests/test_eval18_review_serve.py`, fixture = the exact CLI pipeline a
reviewer inherits (plan → seeded grades → fake judge → `review build`),
with grades arranged so every comparison is a judge-vs-deterministic
disagreement (mandatory stratum — the queue is the whole comparison set,
not a floor sample; `seed_trial_and_grade` gained an additive
`assertions` override for this). AC-1 route enumeration + poisoned-packet
refusal + planted-import lint break; AC-2 one-event capture then
one-event reveal with record-layer refusals between; AC-3 actor/loopback/
side-effect-free/405 posture and no new REGISTERED_EVENTS; AC-4 headless
keyboard drive (hotkeys, integrity gate, auto-advance, progress, needles,
banner); AC-5 dual-server drive over the same experiment — every reviewer
route arm-free while the operator legitimately serves identities, packet
bytes byte-equal to the built file.
