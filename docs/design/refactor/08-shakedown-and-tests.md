# 08 — Shakedown end-state & test architecture (Phases 2, 6)

The shakedown keeps its role — a known-answer validation suite whose seven
layers (`docs/design/shakedown.md`) prove every fence fires — but stops
being an implementation. After the SDK ([02](02-experiment-sdk.md)), images
([03](03-images-and-environments.md)), infra ([04](04-run-engine.md) §1),
and holdouts ([05](05-grading-judging.md) §1) land, each script is thin
choreography: vectors + expected dispositions + `Tally`.

## 1. Graduation ledger (where each hand-rolled capability goes)

| Capability (today) | Home |
|---|---|
| Spec/tasks/run-config/manifest dict authoring (`_harness.py`, both harbor scripts, `tripwires.py`'s 8 deepcopy-mutate sites) | SDK builders ([02](02-experiment-sdk.md)) |
| `bench` subprocess driver + `events`/`event_counts` (`_harness.py:25-60`) | stage APIs + `LedgerView`; scripts may still exec the real CLI for the layers whose *point* is the console script — via one shared helper |
| docker network + proxy lifecycle (`proxy_up/down` ×2) | `hermetic.MeteringProxy` ([04](04-run-engine.md) §1) |
| image builds (raw `docker build` ×3 incl. tests) | `bench images build` ([03](03-images-and-environments.md)) |
| holdout execution + grade injection (`run_holdout` ×2, `inject_grades`, 25 literal writes in tests) | `Holdout.execute` / executing runner ([05](05-grading-judging.md) §1); fake-path injection keeps its first-class fixture name (`write_holdout_results`) |
| rubric with verdict-JSON contract (`harbor_multiagent.py:105-116`) | SDK template ([02](02-experiment-sdk.md) §2) |
| key-presence gating (`official.py:26-28`, `harbor.py:61-66`) | small SDK `require_env_keys(...)` |
| tamper vectors (byte-flip, canonical re-encode, forged lock, holdout-tamper trajectory) | `tests/fixtures/tamper.py` ([01](01-safety-nets.md) §2) — shared by tests and tripwires, **not** public SDK |
| `Tally`, `_run/` staging, layer banners | stay script-local (pytest is the tally in tests; humans need the printed narrative) |

End-state sizes (targets, not caps): `golden.py` ≲40 lines; `official.py`
≲50; `harbor.py` ≲60 with zero docker calls; `tripwires.py` keeps its 18
vector definitions + exact reason strings — that *is* its content — over
SDK mutation helpers. The scripts thereby double as executable SDK
documentation, which is exactly the mission's "examples as docs" ask.

## 2. Test architecture decisions

- **Keep the flat, story-numbered layout.** It is what the specs,
  `ac_coverage.py`, and reviewers key on; subdirectories are already legal
  (`ac_coverage.py:174` uses rglob) if navigation demands it later, but
  nothing forces the churn. AC constraints stay: `test_eval<N>_` file
  prefix and `test_ac<N>_` function prefix never change; moving an AC test
  between stories is two violations by design.
- **Spec `tests:` blocks** (e.g. `docs/design/specs/eval10.spec.md:28-31`)
  are documentation the checker never reads — declare them non-normative in
  the spec header convention, or sweep them during renames; pick one
  (recommend: non-normative note) so they stop silently rotting.
- **Fixture library** is the Phase-0 extraction ([01](01-safety-nets.md)
  §2) plus, from Phase 2, `scenarios.py` reimplemented on SDK builders —
  the `rich_experiment` family becomes ~20 lines each instead of 100+.
- **Deep-import debt:** ~30% of test files bind non-seam paths
  (`HarborEngine._scan_proxy_log`, `judge.client._pos_to_arm`,
  `analyze.report` internals). Each Phase-4/5 decomposition PR fixes the
  import lines it breaks — mechanical, and the re-export facades keep the
  blast radius near zero. No mass pre-emptive rewrite.
- **Two CLI-driving idioms stay two**: CliRunner where the CLI is under
  test; stage APIs/SDK where it is not. The subprocess idiom survives only
  inside shakedown's one helper (its point is the installed console
  script).

## 3. CI end-state

From [01](01-safety-nets.md) §3: `browser` marker replaces path
enumeration; `make shakedown` (hermetic L1+L3) runs as a job; docker and
browser jobs keep their fail-closed `VERDI_REQUIRE_*` switches;
`VERDI_REQUIRE_PROXY` per the Phase-0 decision. Post-refactor additions:
the golden-serialization suite and `bench images verify` on the shipped
official images run in the docker job (image compliance is a product
claim once official images exist).

## 4. Docs end-state (Phase 6)

- `docs/usage-guide.md` gains the SDK path beside the file-authoring path
  (files remain the source of truth for what gets locked — the SDK writes
  them; the guide says so explicitly).
- `docs/images.md` (new, normative image contract) and `docs/engines.md`
  (new, normative engine contract) absorb the folklore; `docs/adapters.md`
  stays the log-format contract.
- `docs/design/shakedown.md` updates its layer table to the thin scripts
  and drops the "reference Squid rejects harbor's credential" caveat once
  the managed proxy ships (the caveat text moves to
  `deploy/metering-proxy/README.md`, where the Squid path lives on).
- README's example spec and quickstart consume the single template
  ([02](02-experiment-sdk.md) §2) — the readme-consistency test extends to
  pin this.

## 5. Acceptance for the whole program

1. `make verify` and `make shakedown` green at every phase boundary; the
   Phase-0 goldens byte-identical throughout (the proof no contract
   moved).
2. The master plan §6 extensibility table hits its targets, measured by
   re-running the audit walkthroughs.
3. `scripts/shakedown/` total drops from ≈1,115 hand-rolled lines to
   ≈300 lines of vectors + assertions, with zero raw docker calls and zero
   hand-built spec dicts.
4. A newcomer can go from empty directory to a graded, judged, analyzed
   fake-engine A/B in under 15 lines of Python — and to a real two-model
   harbor A/B by adding an image name, a proxy context manager, and keys.
