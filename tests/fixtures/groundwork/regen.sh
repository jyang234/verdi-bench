#!/usr/bin/env bash
# Regenerate the committed groundwork grader-plugin fixtures from the planted Go
# modules in this directory. These are the *inputs* to the hermetic mapper tests
# (harness.grade.plugins.groundwork parses groundwork's `review --json`), so they
# must be produced by the REAL pinned binaries once and committed.
#
# Requires flowmap + groundwork on PATH, or VERDI_FLOWMAP_BIN / VERDI_GROUNDWORK_BIN.
# Build them from the sibling verdi-go checkout (pin one build everywhere):
#   cd ../../../verdi-go
#   go build -o /tmp/bin/flowmap ./cmd/flowmap
#   go build -o /tmp/bin/groundwork ./cmd/groundwork
#   VERDI_FLOWMAP_BIN=/tmp/bin/flowmap VERDI_GROUNDWORK_BIN=/tmp/bin/groundwork \
#     tests/fixtures/groundwork/regen.sh
#
# Fixture roles (invsvc reach-trap: base -> reference/violating; alertsvc blind spot):
#   review_block.json     base vs violating  -> verdict BLOCK               (mapped: failed)
#   review_clear.json     base vs reference  -> verdict STRUCTURALLY-CLEAR  (mapped: passed)
#   review_nosignal.json  base vs base       -> verdict NO-STRUCTURAL-SIGNAL(mapped: abstain)
#   review_caution.json   blind spot base vs base -> NO-STRUCTURAL-SIGNAL + standing caution
#   review_unknown.json   HAND-WRITTEN (a synthetic future verdict) — NOT regenerated here.
#
# Volatile-field stripping (determinism):
#   - graphs: the `tool` producer field records the flowmap BUILD version, which
#     churns per build; it is stripped from committed base graphs (mirrors
#     verdi-go/testdata/groundwork/regen.sh). The `stamp` (code identity) is kept.
#   - review JSON: `digest` recomputes from the exact graph bytes (build-dependent)
#     and `algo`/`caveats` are substrate provenance; all are stripped so the
#     committed fixtures pin only the SEMANTIC fields the mapper reads (verdict,
#     new_violations, new_cautions, standing_cautions, service). No mapper test
#     asserts a stripped field.
set -euo pipefail
cd "$(dirname "$0")"

FLOWMAP="${VERDI_FLOWMAP_BIN:-flowmap}"
GROUNDWORK="${VERDI_GROUNDWORK_BIN:-groundwork}"
# flowmap type-checks the module: it needs a writable build cache even when HOME
# is unset (the same requirement the grader plugin sets for its subprocess).
export GOCACHE="${GOCACHE:-$(mktemp -d)/gocache}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

strip_graph_tool() {
	python3 - "$1" <<'PY'
import json, sys
p = sys.argv[1]
g = json.load(open(p))
g.pop("tool", None)  # producing-flowmap-build provenance — volatile, strip it
json.dump(g, open(p, "w"), indent=2, ensure_ascii=False)
open(p, "a").write("\n")
PY
}

strip_review_provenance() {
	python3 - "$1" <<'PY'
import json, sys
p = sys.argv[1]
v = json.load(open(p))
for k in ("digest", "algo", "caveats"):  # build/substrate provenance — volatile
    v.pop(k, None)
json.dump(v, open(p, "w"), indent=2, ensure_ascii=False, sort_keys=True)
open(p, "a").write("\n")
PY
}

# --- reach-trap fixture (invsvc): base graph + review JSONs ---------------------
INV=invsvc
INV_POL="$INV/holdouts/groundwork/policy.json"
"$FLOWMAP" graph --stamp invsvc-base "$INV/base" > "$INV/holdouts/groundwork/base.graph.json"
strip_graph_tool "$INV/holdouts/groundwork/base.graph.json"
BASE="$INV/holdouts/groundwork/base.graph.json"

"$FLOWMAP" graph --stamp invsvc-reference "$INV/reference" > "$TMP/inv-reference.graph.json"
"$FLOWMAP" graph --stamp invsvc-violating "$INV/violating" > "$TMP/inv-violating.graph.json"

# base vs violating -> BLOCK, one new must_not_reach violation
"$GROUNDWORK" review "$INV_POL" "$BASE" "$TMP/inv-violating.graph.json" --json > json/review_block.json || true
strip_review_provenance json/review_block.json
# base vs reference -> STRUCTURALLY-CLEAR (read-only feature, no violation)
"$GROUNDWORK" review "$INV_POL" "$BASE" "$TMP/inv-reference.graph.json" --json > json/review_clear.json || true
strip_review_provenance json/review_clear.json
# base vs base -> NO-STRUCTURAL-SIGNAL (identical graph, body-only)
"$GROUNDWORK" review "$INV_POL" "$BASE" "$BASE" --json > json/review_nosignal.json || true
strip_review_provenance json/review_nosignal.json

# --- blind-spot fixture (alertsvc): base graph + caution (abstain) review JSON --
BS=invsvc/blindspot
BS_POL="$BS/holdouts/groundwork/policy.json"
"$FLOWMAP" graph --stamp alertsvc-base "$BS" > "$BS/holdouts/groundwork/base.graph.json"
strip_graph_tool "$BS/holdouts/groundwork/base.graph.json"
# base vs base -> NO-STRUCTURAL-SIGNAL + a standing caution (reflect frontier blind)
"$GROUNDWORK" review "$BS_POL" "$BS/holdouts/groundwork/base.graph.json" "$BS/holdouts/groundwork/base.graph.json" --json \
	> json/review_caution.json || true
strip_review_provenance json/review_caution.json

echo "regenerated base graphs + json/review_{block,clear,nosignal,caution}.json"
echo "(json/review_unknown.json is hand-written; leave it alone)"
