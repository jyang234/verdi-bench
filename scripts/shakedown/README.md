# shakedown

A known-answer acceptance suite for verdi-bench: positive controls the
instrument must **recover** and adversarial negatives every fence must
**refuse**. The hermetic scripts author + drive experiments in-process through
`harness.sdk` (builders + `ExperimentWorkspace`), asserting each guarantee
fires; the pre-registration refusals still exercise the installed `bench plan`
console script (their point is the CLI's refusal→exit-code mapping). See
[`docs/design/shakedown.md`](../../docs/design/shakedown.md) for the full
design, the capability→vector map, and the honest caveats.

## Hermetic gate (no keys, no Docker)

```bash
make shakedown            # golden.py + tripwires.py
```

- **`golden.py`** — L1: the full pipeline (plan→run→grade→judge→review→process→
  analyze→forensics→verify-chain) on the fake engine + fake judge; asserts the
  known positive control (Δ +0.5, decision MET).
- **`tripwires.py`** — L3: 18 adversarial vectors (pre-registration refusals,
  ledger tamper, analyze fence, cost/insulation/stats, gaming, asymmetric
  contamination); asserts every fence fires with its exact reason.

## Opt-in full-fidelity layers

```bash
# L2 — passing official render with a REAL Anthropic judge
uv run --env-file .env python scripts/shakedown/official.py

# L6 — real LLMs solving tasks in real hermetic containers, egress metered
#      per trial through a proxy this script stands up and tears down
uv run --env-file .env python scripts/shakedown/harbor.py

# L4 — container guarantees on real Docker (builds synthetic images)
VERDI_REQUIRE_DOCKER=1 uv run pytest -m docker -v

# L5 — operator/reviewer/author UIs in a real browser
VERDI_PLAYWRIGHT_PATH=... VERDI_CHROMIUM_PATH=... VERDI_REQUIRE_BROWSER=1 \
  uv run pytest tests/test_eval14_page_drive.py tests/test_eval19_operator_p2.py \
                tests/test_eval18_review_serve.py tests/test_eval17_author.py -v
```

`.env` supplies `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (see `.env.example`).
Generated run state lands in `_run/` (git-ignored); committed inputs are under
`assets/`.

## Layout

| Path | Role |
|---|---|
| `_harness.py` | script-local helpers — `Tally`, `_run/` staging, the one `bench` console-script driver (pre-registration vectors) + ANSI strip; the `events`/`dump_yaml`/`ASSETS` helpers linger only for the not-yet-converted harbor scripts (Phase 3) |
| `golden.py` · `tripwires.py` | hermetic acceptance (L1, L3), authored + driven through `harness.sdk` |
| `official.py` | opt-in real-judge layer (L2), on `harness.sdk` |
| `harbor.py` · `harbor_multiagent.py` | opt-in real-container layers (L6); convert to the SDK in Phase 3 |
| `assets/golden/` | the committed golden vectors (still pinned by a schema test) |
| `assets/harbor/` | the real trial-agent image + minimal metering CONNECT proxy |
