# EVAL-14 implementation plan — operator UI v2

Builds the wireframe record (2026-07-04 session artifact) on the EVAL-13
substrate. Spec: `docs/design/specs/eval14.spec.md`; decisions D001–D004
resolved in `eval14.decisions.ndjson` (D004's trajectory-v3 capture slice is
sequenced separately, behind this story).

## M1 — read seams (AC-1, AC-2 data; AC-6/AC-7 fence)

- `harness/status/aggregate.py` additive fields: `stages.last_event_ts`,
  per-arm `cost` (the RN-2 enforcement figure), judge `pairs_ready` /
  `pairs_expected` (planned cells ÷ arms). EVAL-13 exact-shape asserts
  updated with the feature.
- `harness/status/trial.py`: `trial_detail` — one join per trial (record,
  sha-verified trajectory with status, grades/cant_grades with assertions,
  verdicts via the deterministic `comparison_id_for`, latest forensics
  metrics/flags, quarantine, egress). `None` for an unknown id.
- `harness/analyze/fence.py`: `official_fence_report` — the render fence's
  checks re-projected as named items (ok|failed|unchecked) instead of
  fail-fast raises; side-effect-free; manifest-requiring items are
  `unchecked` without one and block readiness exactly as the render would.

## M2 — serve layer (AC-1, AC-6, AC-7)

- `harness/serve/workspace.py`: `scan_workspace` — one-level ledger scan
  (D003), rows through `compute_status`, withheld-on-tamper.
- `harness/serve/compare.py`: `paired_comparisons` — reuses the judge's own
  `comparisons_from_ledger` (same pairing, same diff artifacts), joins
  grades + verdicts as separate tier lines, difflib line segments for the
  A/B highlight, watermark from `official_fence_report`.
- `harness/serve/server.py`: dual mode (dir | `--root`), `exp` name
  validation (shape + scanned set, never path-joined), routes
  `/api/experiments|status|events|timeline|trial|compare|fence`, `/artifact`
  behind a fixed-name allowlist (analyze outputs only), favicon 204 (a
  `<link>` would break the needle property).
- `harness/serve/cli.py`: `--root`, `--corpus-manifest`.

## M3 — page v2 (AC-3, AC-4, AC-5; D001, D002)

Single-file app in `harness/serve/page.py`: hash router over six screens;
facets, selection, tabs, and compare toggles all URL-encoded (deep links
round-trip); trials master-detail with side panel then enter-to-page (D002);
j/k/enter/esc on list screens; feed on the EVAL-13 byte cursor with
delegated hover-pause, follow-newest, and the N-new pill; `window.__vb()`
as the explicit headless-test seam. Needle-clean: buttons + `window.open`
on relative paths, never anchors.

## M4 — tests (AC-1..AC-8)

- `tests/test_eval14_observability_ui.py`: server-side ACs over a rich
  fixture (real fake-engine run with verified trajectories + heartbeat,
  per-arm workspace content, disagreeing grades, advisory verdict, forensic
  flag, quarantine; `_passing_fence` extends it until every fence item is
  ok).
- `tests/test_eval14_page_drive.py` + `tests/fixtures/browser.py`: headless
  Chromium drives through the global node playwright; skips honestly when
  the stack is absent (the docker-marker precedent).

## Out of scope (unchanged from the spec)

Trajectory-v3 capture (D004 slice, next), reviewer-safe views, mutation
endpoints, auth, SSE.
