<!--
================================================================================
UNTESTED ON GPU -- workspace-pilot kit. Exercise with --smoke first.
No file in this directory has ever run against a real GPU/model/lens. Only the
--offline-selftest paths (stdlib, no torch) are validated. Treat every live path
as UNTESTED until you have run --smoke on a small model. See "Untested surfaces".
================================================================================
-->
# workspace-pilot — P0 offline pilot kit (Track C / Jacobian lens)

Everything the GPU run for **P0** of
[`docs/design/workspace-observability-plan.md`](../../docs/design/workspace-observability-plan.md)
needs, and nothing it does not. P0 is an **offline pilot: no schema freeze, no
harness changes** (plan §6). Its exit is a **written data-shape memo + a candidate
artifact schema**. This kit builds the candidate schema and every input the memo is
written from; **the memo itself is yours to write from the run output** — this kit
does not and cannot produce it.

> **Scope.** Standalone by construction: not part of the verdi-bench `uv` project,
> imports nothing from `harness/`, and nothing under `tests/` imports it. It touches
> no schema, no ledger, and no experiment path — P0 explicitly precedes all of that.

## What is in here

| path | role |
|------|------|
| `capture_workspace.py`      | load model + lens, run prompts, emit candidate `workspace_trajectory.json` (+ sidecar) |
| `score_eval_awareness.py`   | the A.21 eval-awareness detector; re-scores an artifact offline (deterministic) or captures live then scores |
| `schema/workspace_trajectory.draft.schema.json` | the candidate artifact JSON Schema (DRAFT) |
| `data/control_nouns.txt`    | the frozen 100-noun A.21 control set (pinned by `mapping_version`) |
| `prompts/`                  | the pilot prompt set + `manifest.json` (4 corpus tasks, 4 realism pairs, 3 deployment controls) |
| `sample_data/`              | tiny hand-built fixtures + `make_samples.py`; the substrate for `--offline-selftest` |
| `requirements.txt`          | GPU-box-only deps (do **not** add to `pyproject.toml`) |
| `_wtcommon.py`              | shared deterministic IO / hashing / schema helpers (single source of truth) |

The candidate schema is documented field-by-field in
[`docs/design/workspace-trajectory-schema-draft.md`](../../docs/design/workspace-trajectory-schema-draft.md).

## Step 0 — run the offline self-tests FIRST (no GPU needed)

These are the only validated paths. Run them on any box before touching a GPU; they
import no torch and exercise parsing, scoring, serialization, truncation disclosure,
and the fail-closed guards on committed sample data.

```bash
cd scripts/workspace-pilot
python3 -m py_compile _wtcommon.py capture_workspace.py score_eval_awareness.py \
    sample_data/make_samples.py prompts/make_manifest.py
python3 capture_workspace.py --offline-selftest
python3 score_eval_awareness.py --offline-selftest
```

Both must print `PASSED`. If you install `jsonschema` (optional), the capture
self-test upgrades from the structural fallback to full JSON Schema validation and
says so (`validation (jsonschema): OK`).

The committed fixtures and prompt manifest are reproducible:

```bash
python3 sample_data/make_samples.py       # regenerates sample_data/*.json
python3 prompts/make_manifest.py          # regenerates prompts/manifest.json (sha index)
```

## Hardware expectations

| run | model | VRAM (rough) | purpose |
|-----|-------|--------------|---------|
| `--smoke` | Qwen3.5-4B + published Neuronpedia lens | ~10 GB | **data-shape only** — wire-check the pipeline (plan **D3**: 4B understates workspace phenomena) |
| primary   | **≥27B-class** open-weights (the review's replication tier) | 1× 80 GB-class or multi-GPU | the run the memo is actually written from (plan **C1/D3**) |

`--smoke` is not a calibrated result — it exists to prove the capture → score →
package loop end to end on cheap hardware before you spend the big run.

## Install on the GPU box

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r scripts/workspace-pilot/requirements.txt
# jlens is a git install (unmaintained reference impl -- PIN A REVISION, do not track HEAD):
pip install "git+https://github.com/anthropics/jacobian-lens@<COMMIT_SHA>"
```

Record the resolved versions (`pip freeze | grep -E 'torch|transformers|accelerate'`)
and the exact jlens commit sha — they are part of the run's provenance.

## Command sequence (fit-or-download lens → capture → score → package)

**1. Get a lens.** Either download a pre-fitted Neuronpedia lens (preferred; pin its
sha256) or fit one (cheap — the paper reports n≈10 prompts nearly matches n=1000):

```bash
sha256sum <lens_artifact>            # record this; it goes in the pinned tuple
```

**2. Capture** one artifact per prompt. Pin the model revision (**required** unless
you pass `--allow-unpinned`, which records the bypass):

```bash
# smoke first (4B, data-shape only):
python3 capture_workspace.py --smoke --model-revision <REV> \
    --lens-path <lens_artifact> --out runs/smoke
# primary (>=27B):
python3 capture_workspace.py --model <HF_ID> --model-revision <REV> \
    --lens-path <lens_artifact> --layer-band depth_fraction:0.40-0.70 \
    --top-k 16 --out runs/primary
```

`capture_workspace.py` reads `prompts/manifest.json`, verifies every prompt's sha
(fails loud on drift), resolves the layer band against the model's real depth, emits
a time-free `<id>.workspace_trajectory.json` per prompt plus a `.sidecar.json` that
carries `captured_at` (the only wall-clock, kept out of the body), and validates each
artifact against the draft schema (warns-and-continues if `jsonschema` is absent).

> **Adapter note (UNTESTED).** `_extract_raw()` — the one function that turns a jlens
> `apply()` result into the artifact's frames — deliberately **raises** until you
> adapt it to the installed jlens result shape. This is a guard, not a bug: it
> prevents a silently-wrong artifact. Adapt it, then confirm on `--smoke` output
> before the ≥27B run.

**3. Score** the A.21 detector offline (deterministic; the artifact is the auditable
object):

```bash
python3 score_eval_awareness.py --artifact runs/primary/*.workspace_trajectory.json \
    --out runs/primary/scores
```

This writes `eval_awareness_scores.csv` (per transcript) and
`eval_awareness_positions.csv` (per position). Scoring is disclosure-only: it prints
raw scores and **applies no gating threshold** (thresholds are per-(model, lens,
tokenizer) and calibrated later). It **abstains** where probe tokens are
under-resolved rather than guessing.

**4. Package** and send back:

- every `runs/*/*.workspace_trajectory.json` (+ `.sidecar.json`)
- every `runs/*/scores/*.csv`
- the resolved model revision, the lens sha256, and `prompts/manifest.json`'s sha256

Those files **are the inputs to the P0 data-shape memo** (artifact sizes, band
cleanliness, score distributions, eval-vs-real separation on the realism pairs,
which probe words dropped as multi-token). The memo is written by a human from them.

## Pinning discipline (plan D4 / C2)

Every artifact carries the pinned tuple `{model_revision, lens_sha256,
mapping_version}` and the prompt-set provenance `{manifest_sha256, prompt_id,
prompt_sha256}`. Reproducibility requires that you additionally record, next to the
run: the exact resolved dependency versions, the jlens commit sha, and the control
set's sha256 (`sha256sum data/control_nouns.txt`). GPU/sampling nondeterminism means
the **committed artifact**, not the live run, is the reproducible object — all
detectors are deterministic functions of it.

## Untested surfaces (honest labeling)

Validated here (no GPU): argument parsing, layer-band resolution, single-token probe
resolution + multi-token disclosure, top-k deterministic sort/truncate, position-cap
truncation disclosure, artifact assembly + canonical serialization, schema validation
(structural fallback always; full `jsonschema` when installed), prompt-manifest sha
verification, the full A.21 scoring math incl. abstain and trust guards, and CSV
determinism.

**NOT validated here — exercise with `--smoke` before trusting:**

- **`_load_model_and_tokenizer`, `_load_or_fit_lens`, `_lens_apply`** — every
  torch / transformers / jlens call. Written defensively against API drift, but the
  jlens API this kit targets (`jlens.from_hf`, `JacobianLens.from_pretrained`,
  `lens.apply(model, prompt, positions=...)`) has never been called here.
- **`_extract_raw`** — the jlens-result → frames adapter; it raises by design until
  adapted on the GPU box.
- **`score_eval_awareness.py --live` / `--smoke`** — delegates to the capture path
  above, so it inherits all of its untested surface.
- **Tokenizer specifics** — whether `fictional` / `simulation` and each control noun
  resolve to a single token is per-tokenizer and only observable on real hardware;
  the kit discloses drops but cannot predict them.
- **VRAM figures** above are rough estimates, not measurements.
