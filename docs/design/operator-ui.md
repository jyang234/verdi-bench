# Operator UI design

The design contract for `harness/serve/static/` (the operator page). Code
comments cite it as `[operator-ui §<section>]`. Every visual decision derives
from this file; deviations get written back here first. (Prototyped in the
untracked `prototype/` sandbox; promoted 2026-07-06.)

## Concept

The operator view is the **instrument face** of a benchmark-grade A/B
instrument: a read-only readout over a hash-chained ledger. The aesthetic is a
precision instrument, not a marketing dashboard — calm paper surfaces, exact
tabular numerals, one loud element per screen, and the instrument's own
vernacular (arms, pairs, cells, verdicts, evidence, chain). The signature
element is the **pair tape** (below): the experiment's sign test rendered as a
strip of cells, recognizable at a glance like a punched tape from the machine.

Non-negotiable product invariants (inherited from the harness, keep exactly):

- Single self-contained document: inline CSS/JS, **no external references of
  any kind** (no fonts, no CDNs, no `href=`/`src=` URLs — the needle property).
- Vanilla JS with the existing `h()` builder, hash router, poll-render loop.
- Every meaningful view state lives in the URL.
- Honesty: unmeasured is never zero and never failure-red; broken chain fails
  closed to WITHHELD; judge output is always marked ADVISORY; denominators are
  stated when tallies exclude pairs.

## Tokens

Keep the existing surface/ink/status tokens (they are the validated reference
palette). Changes and additions:

```css
/* arm identity — the ONLY colors that may mean "arm A" / "arm B".
   Validated pair: CVD worst-case ΔE 96.7 light / 97.3 dark. */
--arm-a: #2a78d6;            /* dark mode: #3987e5 */
--arm-b: #eb6834;            /* dark mode: #d95926 */
--arm-a-soft: rgba(42,120,214,0.12);   /* row/cell washes; dark: rgba(57,135,229,0.16) */
--arm-b-soft: rgba(235,104,52,0.12);   /* dark: rgba(217,89,38,0.16) */

/* agreement (pair tape cells where arms agree) — recessive by design */
--agree-fill: #c3c2b7;       /* both pass; dark: #52514e */
--agree-empty: #e1e0d9;      /* neither passes (outline style); dark: #383835 */
```

`--meter` stays blue; **`--meter2` (violet) is retired** — anything that meant
"the other arm" becomes `--arm-b`. Status good/warning/critical are reserved
for pass/fail/health and are never used for arm identity; arm colors are never
used for pass/fail.

Agent lanes (process view): categorical slots in fixed first-seen order —
blue `#2a78d6`, aqua `#1baf7a`, yellow `#eda100`, violet `#4a3aa7`, orange
`#eb6834`, magenta `#e87ba4` (dark: `#3987e5 #199e70 #c98500 #9085e9 #d95926
#d55181`). Agents beyond six fold into muted ink. The agent NAME is always
printed beside its color (relief rule — color never carries identity alone).
Note the overlap with arm hues is deliberate and safe: agent lanes appear only
inside a single trial (one arm), never beside arm-colored elements.

## Type

- Body: system-ui stack, 14px/1.45 (unchanged).
- **Instrument voice = ui-monospace**: ids, numbers, event kinds, timestamps,
  and *eyebrow labels* (11px, uppercase, letter-spacing 0.08em, `--ink-3`).
  Eyebrows name sections the way the ledger names events.
- Display numerals: verdict-card counts at 30–36px weight 700,
  `font-variant-numeric: tabular-nums`.
- Never color body text with a series/arm color; a colored mark sits beside
  neutral-ink text.

## Signature: the pair tape

One cell per (task, repetition) pair, grouped by task with a 6px gutter between
task groups, 14×14px cells with 2px gaps, square (2px radius). Cell states:

| state | rendering |
|---|---|
| A only passed | solid `--arm-a` |
| B only passed | solid `--arm-b` |
| both passed | solid `--agree-fill` (quiet — agreement recedes) |
| neither passed | 1.5px inset border `--agree-empty`, transparent center |
| not fully graded | 1px dashed border `--hairline`, transparent |

Interactions: hover = 1px `--ink-1` ring + tooltip (`t3 · rep 1 — only
treatment passed`); click = navigate to that pair (compare screen scroll/slice).
A one-line legend renders beneath the tape, always (relief rule). Task ids
label groups beneath in 10px mono when ≤ 24 groups.

The tape appears on the experiment overview (inside the verdict card) and atop
the compare screen — same component, same data derivation as compare.py's
arithmetic (the pairTallies/slicePred precedent: tape counts must equal the
tallies and the filtered row counts).

## Screen recipes

### Overview = verdict card first

Order: header bar → **verdict card** → stage rail (kept, slimmed) → tiles
(kept) → feed (demoted).

Verdict card anatomy (Dynatrace problem-card lineage):

```
┌─ EYEBROW: RESULT SO FAR · HOLDOUT (DETERMINISTIC) · 24 PAIRS ─────────────┐
│  ● treatment 18/24        ● control 6/24        → treatment leads          │  ← 32px numerals, arm dots
│  [pair tape ................................................]              │
│  legend: ■ treatment only  ■ control only  ■ both  □ neither  ┄ ungraded   │
│  JUDGE (ADVISORY): treatment 12 · control 0 · tie 12 → leans treatment     │  ← one quiet line
│  3 pairs not fully graded — excluded from tallies                          │  ← only when true
└────────────────────────────────────────────────────────────────────────────┘
```

Left border 3px `--arm-a`→`--arm-b` gradient (the card's A/B mark). No red/
green: leading is identity, not virtue.

Feed demotion: the ledger feed moves under a collapsed-by-default card
("Ledger feed · N events · M kinds"), open state in the URL (`?feed=1`).
Event kinds get operator vocabulary via a display map (internal name stays in
the row's `title`): `trial → trial finished`, `grade → graded`,
`cant_grade → grading refused`, `judge_verdict → judge verdict (advisory)`,
`process_score → process scored`, `trial_infra_failed → infra failure`,
`experiment_locked → plan locked`, `executed_order → run order realized`,
`forensics_report → forensics scan`, `chain_anchor → chain anchored`, etc.
Consecutive runs of the same kind collapse to one row (`process_score × 16`)
that expands on click. The unblinded-operator banner shrinks to one line of
12px `--ink-3` text with the ⚠ chip — present, legible, no longer the loudest
element on the page.

### Compare = paired grid, counters as results

Order: header bar → verdict banner (same numbers as overview, compact) → pair
tape → tally strip (restyled as *results that filter*: each tally is a small
stat chip — count in 16px mono bold, label beneath in eyebrow type; active
slice = `--soft` wash + meter ring) → pairs table → diff cards.

Pairs render as ONE table (built 2026-07-06, revising the earlier index-table
+ separate-cards plan: once cards collapse to a scan line they duplicate the
index, so the table row IS the collapsed pair). Row wash by outcome class
(`--arm-a-soft`/`--arm-b-soft`, agreement rows unwashed). Columns: task·rep ·
A holdout · B holdout · judge (ADVISORY) · disagreement · chevron. Header row
carries aggregates under each column (`18/24 pass`, judge tallies, 11px mono).
A row expands in place (URL `?open=id,id`, same pattern as `?fr=`); `expand
all / collapse all` sit at the table head. Expanded body: judge verdict chip
with the reason as a quoted block FIRST, then armhead, two-pane diff,
reasoning drawer. Tape cells on this screen open their pair in place and
scroll to it. Default order is task order; "only disagreements" chip is the
disagreement lens (no extra sort).

### Trial process = the flight-recorder timeline

The strongest data in the product; render it like an instrument trace.

- **Left time ruler**: 44px mono column, a tick per step at its `relative_ts`;
  ruler line in `--hairline`. Unknown ts = "—" slot below the last known tick
  (never interpolated).
- **Agent color**: 3px left border + agent name chip on every row, colors from
  the agent-lane slots in first-seen order. A sticky legend strip at the top
  lists agents with their colors and turn/token subtotals; clicking an agent
  name dims other agents' rows (filter state in URL `?agent=`).
- **Thought vs action**: thoughts keep the soft wash + "thought" glyph;
  actions carry their kind glyph (mono: `edit`, `run`, `msg`) and stay on the
  surface. Long thought bodies clamp to 6 lines with an inline "show all"
  (expanded set in URL).
- **Token bars**: right-aligned 64px track per row; bar width ∝ tokens scaled
  to the trial max, 4px tall, 2px radius, in the row's agent color at 55%
  opacity with the exact count in 11px mono beside it. No tokens = no bar, no
  zero (honesty).
- Trial header gets the pair verdict + telemetry as now, but absent telemetry
  compresses to `cost — · wall — · tokens —` with one title tooltip
  ("— = not measured"), not a sentence of "not measured"s.

### Trials = the work first, the id last

One filter card (facet chips + typed grammar on the first row, saved views on
a hairline-separated second row — all test-seam ids preserved), then the
table. Column order: task · arm (with identity dot; A = the spec's first arm,
the same mapping compare and the verdict card use) · rep · outcome · grade ·
cost · wall · flags · trial id (mono, dimmed, full id in the title). Visible-
row aggregates ride the column headers: grade gets "N pass · M fail (·
pending/can't)", cost gets the measured total. Unmeasured cells are `—` with
a per-cell title and ONE footnote under the table. `cant_grade` displays as
"can't grade" (internal value in the title).

### Home = same identity language

Arm dots on the arms column (per-experiment: A is that spec's first arm, so
the dot colors always agree with the experiment's own compare screen), models
beneath in muted ink, card heading in eyebrow voice. Everything else keeps
its lifecycle-state semantics untouched.

### Everywhere

- "not measured" cells render as `—` with `title="not measured"`; the phrase
  appears once per table in a footnote line, not per cell.
- Empty states say what would fill them ("No trials yet — nothing has run.").
- Focus-visible outlines and j/k/enter/esc keyboard behavior are preserved on
  all new interactive elements (tape cells, collapsed rows, agent chips).
