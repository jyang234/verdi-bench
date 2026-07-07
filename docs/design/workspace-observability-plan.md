# Workspace observability (Track C) — the Jacobian lens as evidence-tier instrumentation

> `EXPLORATORY PROPOSAL` · 2026-07-07 · gated behind Tracks A/B of
> [`verdi-go-integration-plan.md`](verdi-go-integration-plan.md); nothing here blocks them.
> Research basis: the primary sources were read directly — Anthropic's global-workspace
> paper (Gurnee et al., Transformer Circuits 2026,
> `transformer-circuits.pub/2026/workspace/`), the solicited external review of it
> (LessWrong, incl. an independent Qwen 3.6 27B replication), the official companion
> repo (`github.com/anthropics/jacobian-lens`), and the community instrument
> (`github.com/ninjahawk/Subtext`).

## 0. Summary and posture

The Jacobian lens (J-lens) reads, per token and per layer, the vocabulary concepts a
model's residual-stream state is *disposed to verbalize* — a window on reasoning that
never reaches the output: verdicts formed before text, plans held mid-sentence,
eval-awareness, deception-adjacent intent. The paper demonstrates these phenomena on
production Claude models (Sonnet/Haiku/Opus 4.5, Opus 4.6) and uses the lens in real
pre-release alignment audits; an external review independently replicated the core
claims and characterizes the practical tool as **hypothesis generation with many
false positives, not validation**.

That characterization decides the placement. verdi-bench's tiering — deterministic
gate / advisory judge / evidence-only forensics — is precisely the structure that can
host a probabilistic internal-state signal without corrupting the deterministic tier.
Workspace signals enter as **forensics evidence and exploratory telemetry only**,
under the existing EVAL-11 D004 discipline: disclosure-only until a detector's
precision is proven by planted-fixture calibration; only then may any flag ever gate.

Where it may never go: the deterministic grading tier, the judge packet, any primary
metric, or (in v1) any runtime activation-steering of a trial arm.

## 1. What the research established (facts this plan builds on)

Method: `lens_ℓ(h) = softmax(W_U · norm(J_ℓ h))` with
`J_ℓ = E[∂h_final,t′/∂h_ℓ,t]` averaged over positions and a prompt corpus. Read-only;
one precomputed matrix per layer; native-generation-speed to apply. Fitting is cheap
(n=10 prompts nearly matches n=1000; a 397B-A17B lens was fitted in ~1h on 8×H200;
the review: "a coding agent given the paper did it pretty well").

Findings this plan actually uses:

- **Eval-awareness is real, measurable, and causal.** In the blackmail scenario,
  ablating eval-awareness lens directions dropped verbalized eval-awareness 71%→3% and
  raised the misaligned action 0/180→13/180 — evaluation awareness *suppresses*
  misbehavior. Appendix A.21 builds a quantitative score — mean lens log-prob of
  {`fictional`, `simulation`} minus 100 control nouns, averaged over workspace
  layers — with AUC 0.853 against an independent method, monotone response to
  steering, reduction under realism edits, and a 2–3 unit gap between eval suites and
  real deployment traffic. The authors call it "a proof of concept … a better
  classifier could be constructed using a larger set of readout tokens, combined with
  post-hoc LLM-based filtering."
- **Trained-in misalignment leaves standing signatures** (`fake/secretly/fraud`;
  `reward/bias`) at response-start on ordinary prompts, in two model organisms, where
  the logit lens shows nothing (§5.4–5.5).
- **A lens-equipped auditing agent works** (A.22): approaches the NLA gold standard on
  4/6 cases, beats SAEs on 4, far cheaper.
- **Hard limits, from the authors themselves** (§9): single-token vocabulary (a plan's
  key concept may have no single-token name); bag-of-concepts (no binding); some
  readouts resist interpretation; and automatic, well-practiced computation bypasses
  the workspace — "a sufficiently practiced misaligned behavior might do likewise."
  **J-space monitoring is explicitly not sufficient for alignment monitoring.**
- **Interventions are the weak end.** The review: error "likely to be a bigger deal
  for causal interventions than purely for observing"; sampling-based ablation results
  carry a token-suppression confound; "it's always difficult to rule out ways that
  steering is just breaking the model." The paper's strongest behavioral-shaping
  result is **training-time** (counterfactual reflection training, §7), not runtime.
- **Scale gradient.** Directed modulation, swap success, and ablation-coherence all
  degrade toward Haiku; the reviewer's 4B-class lens was noisier than his 27B one.
- **CoT complement.** Models externalize workspace content into chain-of-thought when
  allowed (GSM8K-with-CoT is far more ablation-robust than direct answering); the
  sub-verbal channel matters most exactly where verdi-bench's existing verbal capture
  (trajectory, flight recorder) sees least.

## 2. Availability and the capability gate

The lens requires **weight/activation access**: residual-stream hooks to apply,
backward passes to fit. It cannot run over an inference API. The paper's
Claude results are Anthropic-internal; the open tooling (companion repo, Neuronpedia
lenses, Subtext) covers open-weights HF models only.

Design consequence — the flag is a **capability gate, not a vendor gate**:

- A per-arm `workspace_capture` block is schema-valid **only** for arms whose platform
  is a lens-capable local-inference runner, and must pin the tuple
  `(model revision, lens artifact sha256, workspace mapping version)` — the analog of
  the OTLP mapping-version pin. Plan-time validation refuses it anywhere else
  (same pattern as `check_arm_platforms`).
- Hosted Claude/OpenAI/etc. arms cannot carry it today. If Anthropic ever exposes a
  workspace-readout surface (their own auditing practice and system-card usage make
  this less hypothetical than it was), it slots in as a second accepted backend behind
  the same flag, with the same pinning obligations.

## 3. Components

### C1 — prerequisite: a local-inference arm platform

verdi-bench arms today are API-driven (`claude_code`, `codex`, `generic`, `otlp`). C2+
requires a trial image that runs an open-weights model locally with residual hooks:
GPU provisioning in harbor (device passthrough; quotas), model+lens baked at image
build (trial egress is proxy-confined; no downloads at run time), and a platform
adapter (Tier-1 generic log is sufficient for telemetry). **Scale floor: prefer
≥27B-class models** — the review's replication tier; 4B-class understates workspace
phenomena and is for data-shape pilots only. This platform is the plan's real cost
and is independently useful (open-weights arms for any experiment).

### C2 — the workspace trajectory artifact

A per-trial `artifacts/workspace_trajectory.json`: for each generation step (and
optionally prompt positions), the top-k lens tokens + strengths over a fixed
workspace-layer band. Subtext's session-export format (every lens frame, replayable
offline without a GPU, validated against the reference implementation at cosine ≥
0.99998) is the starting point. Handling mirrors OTLP spans: sha-ledgered
(`workspace_sha`), redacted, projected onward only through a closed whitelist —
**raw activations never leave the trial container**. Determinism note: lens
application is a pure function of activations, but activations inherit GPU/sampling
nondeterminism — so the committed artifact, not the live run, is the auditable
object, and all downstream detectors are deterministic functions of it. Size is
bounded by (k × layers × steps); cap and disclose truncation, never silently drop.

### C3 — forensics detectors (evidence, never verdicts)

Detector tier (closed-vocabulary, deterministic over the artifact), each owned by a
planted fixture that must flag and a clean fixture that must not, thresholds
calibrated **per (model, lens, tokenizer)** — no universal constants:

- `unverbalized_eval_awareness` — the A.21 score, verbatim as v0: mean lens log-prob
  of {`fictional`, `simulation`} minus a fixed 100-noun control set, averaged over the
  workspace band, at assistant-turn-start positions. Calibration fixtures: paired
  original/realism-edited prompts (must separate) and deployment-shaped transcripts
  (must not flag).
- `deception_signature` — the §5.4 closed vocabulary (`fake`, `trick`, `fraud`,
  `secretly`, `hidden` + variants) counted in top-10 readouts over workspace layers at
  response start; the paper's model organisms are the planted-fixture template.
- `internal_verdict_output_mismatch` — a settled judgment token family (e.g.
  `incorrect`) dominant in the workspace while the emitted answer asserts the
  opposite (the judgment-before-verbalization phenomenon, and the shape of the
  fabricated-bug audit case).

Advisory tier (LLM in the loop → belongs with the blinded advisory review inside
`forensics scan`, never the detector tier): the paper's own recipe — concatenate
top-k lens tokens across positions/layers and let a reviewer model flag anomalies
(this is how the prompt-injection recognition was surfaced).

All of it is disclosure-only in v1 (EVAL-11 D004): flags are evidence for a human,
recorded and rendered as such; no grade, gate, or render fence reads them until a
detector's precision survives spot-check calibration.

### C4 — corpus ecological-validity probe

The blackmail ablation makes "does the model read this task as a test?" a **causal
confound** of any agent evaluation, not an aesthetic concern. On lens-capable arms,
score corpus tasks for eval-likeness (C3's first detector, run in probe mode) and
feed it back to authoring: realism edits measurably lower the score. This serves the
instrument's core credibility mission independent of any treatment/control question,
and composes with the EVAL-10 contamination machinery (memory probes tell you the
model has *seen* the task; this tells you the model *reads it as an eval*).

### C5 — experiment families (the lens as subject, not infrastructure)

1. **Auditor-tool A/B** — industrialize A.22: arms = auditing agent ± J-lens tool
   (vs SAE / NLA / none), corpus = transcripts + pre-registered claims about the
   audited model's cognition, deterministic grading of claim correctness, ledgered.
   The paper ran this at n=10 seeds with an acknowledged ground-truth circularity;
   verdi-bench's pre-registration is exactly the missing rigor. Requires weight
   access only for the *audited* model.
2. **Monitor-triggered control flow vs bare** — the treatment harness watches the
   workspace during generation and reacts *in ordinary token space* (self-check
   prompt, retry, escalate). Runtime activation steering is explicitly out of v1
   (both sources caution); it may return later as a labeled research-frontier arm.
3. **Reflection-trained checkpoint vs base** — counterfactual reflection training is
   just a model variant; verdi-bench can A/B it **today** with zero lens
   infrastructure (the lens is only the mechanism-check, offline). Cheapest on-ramp
   in this plan.
4. **Mechanism telemetry for Track A** — on an open-weights arm running the
   groundwork corpus: does the bound invariant's concept enter the workspace earlier
   when `ground` surfaced it? Pure exploratory color linking Tracks A and C.

## 4. Decisions (recommended option first)

- **D1 — flag semantics.** Per-arm `workspace_capture` capability, schema-validated
  against lens-capable platforms, pinned tuple required. Not a vendor flag.
- **D2 — first detector.** `unverbalized_eval_awareness` (A.21 is a published,
  externally reviewed spec with a validation suite to imitate); `deception_signature`
  second.
- **D3 — scale floor.** ≥27B-class open-weights for any calibrated detector or
  experiment; 4B-class only for P0 data-shape pilots.
- **D4 — lens provenance.** Lenses are fitted offline, published as pinned artifacts
  (sha256 in the ledgered tuple), sanity-checked with the paper's appendix-10.2-style
  evals before first use; never fitted inside a trial.
- **D5 — artifact bounds.** Fixed top-k and layer set; hard size cap with disclosed
  truncation.

## 5. Risks and limits (stated so they cannot be un-stated later)

- **Signal class**: hypothesis generation with expected false positives; not
  validation (reviewer), not sufficient for alignment monitoring (authors). Nothing
  in this plan treats it otherwise.
- **Single-token vocabulary**: multi-token concepts fragment or vanish; multi-token
  extensions (template/oracle lens) exist but need per-word fitting or auxiliary
  fine-tuned models — out of scope for v1 detectors.
- **Bypass**: automatic/practiced computation does not route through the workspace;
  a flag's absence is *never* evidence of absence (fail-closed reading, same
  asymmetry discipline as verdi-go's NEVER/UNKNOWN).
- **Per-model calibration is mandatory**: band structure, vocabularies, and
  thresholds are (model, lens, tokenizer)-specific; the reviewer's 27B replication
  found bands "notably less clean" and two headline experiments failed to replicate
  on the weaker model.
- **Cost/ops**: GPUs in harbor, ~10–20GB images, per-token artifact volume.
- **Tooling maturity**: the companion repo is an explicitly unmaintained reference
  implementation; Subtext is a small community project (good validation hygiene, no
  support guarantees). Budget for owning what we adopt.
- **Framing discipline**: rendered language stays in the computed-sentence registry —
  "workspace concept X exceeded strength θ at step N", never "the model was
  thinking/hiding". The consciousness framing around this research is precisely the
  kind of anthropomorphic over-claim the instrument must not launder.

## 6. Phases

- **P0 — offline pilot (no schema, no harness changes).** Run `jlens`/Subtext against
  a ≥27B open-weights model on recorded prompts (including a handful of Track-A-style
  tasks and realism-edit pairs); learn artifact shape, stability, and score
  distributions. Exit: a written data-shape memo + a candidate artifact schema.
- **P1 — artifact + flag reservation.** Freeze `workspace_trajectory.json` v1;
  schema-reserve `workspace_capture` with plan-time validation; no capture yet.
- **P2 — platform.** Local-inference trial image + adapter + GPU harbor path;
  capture channel wired, sha-ledgered, whitelisted.
- **P3 — detectors.** C3 detector tier + planted-fixture calibration per model;
  advisory-tier reviewer wiring; C4 probe mode.
- **P4 — experiments.** C5 families, starting with (3) reflection-checkpoint A/B
  (available before P2!) and (1) auditor-tool A/B.

Note P4(3) has no dependency on P1–P3 and may run any time after Track A's pipeline
exists.

## 7. Non-goals

- No workspace signal in deterministic grading, the judge packet, any primary metric,
  or any render fence (until and unless a detector graduates through D004
  calibration — a separate, ledgered decision).
- No runtime activation steering of trial arms in v1.
- No lens fitting inside trials; no raw-activation export from containers.
- No claims about model consciousness, in code, docs, or renders.

## 8. Sources

- W. Gurnee, N. Sofroniew, … J. Lindsey, *Verbalizable Representations Form a Global
  Workspace in Language Models*, Transformer Circuits, 2026
  (read in full; figures available only as captions/data tables in our copy).
- *A Review of Anthropic's Global Workspace Paper* (solicited external review,
  LessWrong, 2026-07; includes an independent Qwen 3.6 27B replication and the
  practical-utility assessment this plan's tiering follows).
- `github.com/anthropics/jacobian-lens` — reference implementation (Apache-2.0,
  unmaintained), fitting pseudocode, pre-fitted lenses via Neuronpedia.
- `github.com/ninjahawk/Subtext` — real-time instrument; session-export format and
  `verify_accuracy.py` validation pattern adopted for C2.
