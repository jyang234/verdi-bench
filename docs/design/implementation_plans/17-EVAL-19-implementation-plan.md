# EVAL-19 implementation plan — operator UI P2

Spec: `docs/design/specs/eval19.spec.md`; decisions D001–D003 resolved in
`eval19.decisions.ndjson` (serve `--bundle` flag, localStorage views with
the URL canonical, facet-scoped grammar v1).

## M1 — static bundle (AC-1)

`harness/serve/bundle.py:write_bundle` — the operator view in archive
form. The live page reserves one line (`const BUNDLE = null;`) as the
export seam; `write_bundle` gathers every read the page can ask for
(status, the full event tail + cursor, timeline, per-trial detail,
compare, fence — the exact `/api/…` payloads) and replaces that line with
the data object. The page's data helper short-circuits to `BUNDLE` and
the poll loop never re-arms, so the same single-file document renders
every screen from `file://`. Determinism is by construction (sorted-key
ASCII JSON over pure file-state reads, no wall clock); the needle
property carries to the archive by *inerting* the embedded JSON — each
needle substring gets one `\uXXXX` character escape, so `JSON.parse`
restores the data (rendered via `textContent`) while the document bytes
carry no external or active reference. The archive says what it is: the
banner gains a static-bundle sentence, the bar shows STATIC BUNDLE, the
findings screen states that artifacts are not embedded. `bench serve
<dir> --bundle <out>` (D001) writes the file and starts no server;
`--root` is refused (a bundle archives one experiment).

## M2 — typed filter grammar (AC-2)

A closed parser inside the single-file page: `field:value` over the five
facet fields, `-field:value` negation, `*` wildcards on id-like fields
(task) and free-text words, bare words as free text. Grammar and chips
are two projections of one URLSearchParams state — negation rides inside
the param value (leading `-`), the input renders the canonical
serialization of the params, the selected chips render the grammar
(negation included). Malformed input raises a named parse error shown in
place; the previous filter stays applied — never a partial guess. A `?`
affordance lists the productions on the page itself.

## M3 — saved views (AC-3)

A view IS a stored URL fragment: `localStorage` holds `{name, hash}`
pairs (D002 — the server stays structurally read-only), restore is
`location.hash = stored`, rename/delete edit the local list, duplicates
are refused with the reason. The UI states the trust boundary: the URL is
the canonical, shareable form.

## M4 — honest small multiples (AC-4)

ETA derives client-side from trial-event completion timestamps: below
three completions (or with nothing remaining) it is ABSENT — not zero,
not a dash; when shown it is labeled approximate with its sample size.
Per-arm cumulative-cost sparklines render as inline SVG built through the
namespace of a static `<svg>` prototype (no namespace URI string in the
page bytes): a null cost contributes no point and breaks the line — a
visible gap, never a zero; an all-null arm says "no measured costs".

## M5 — tallies as navigation (AC-5)

Compare-summary counts become chips whose predicates mirror
`compare.py`'s summary arithmetic exactly, so a tally always equals its
filtered row count; the slice lives in the URL (`slice=holdout:b_only`,
`judge:unjudged`, …) and an unknown slice value is named and ignored,
never guessed. Forensic flag chips on trial rows deep-link to that
trial's forensics tab.

## M6 — tests

`tests/test_eval19_operator_p2.py`, reusing EVAL-14's `rich_experiment`
and the headless drive. AC-1 byte-determinism, CLI parity, needle scan,
dir-digest/no-event invariance, and a full `file://` screen walk; AC-2
grammar↔chips URL-state equality, production round-trips, and four named
parse errors leaving the filter intact; AC-3 save/duplicate-refusal/
restore/reload/rename/delete with the directory digest unchanged; AC-4 a
mid-run fixture (6 of 8 cells, a null mid-series) asserting the exact ETA
object, cumulative spark points, the gap-broken path, and the absent-ETA
two-trial case; AC-5 tally slices with reload restore and the flag
deep-link; AC-6 the bundle seam's uniqueness, planted-import contract
break, unchanged REGISTERED_EVENTS/entrypoints, and the GET-only posture.
