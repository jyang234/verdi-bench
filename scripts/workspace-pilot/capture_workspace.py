# ============================================================================
# UNTESTED ON GPU -- workspace-pilot kit. Exercise with --smoke first.
# This file has NEVER run against a real GPU/model/lens. Every torch /
# transformers / jlens path below is UNVALIDATED and written defensively against
# API drift, with actionable errors. Only --offline-selftest (stdlib, no torch)
# is exercised here. See README.md "Untested surfaces" before trusting a live run.
# ============================================================================
"""Capture one candidate ``workspace_trajectory.json`` per prompt/session.

Implements the C2 artifact of workspace-observability-plan.md (P0 offline pilot):
loads an HF model + a pre-fitted Jacobian lens, runs a prompt set, and emits a
deterministic, time-free artifact body plus a wall-clock sidecar. The artifact
carries, per position, top-k (token, strength) frames over a declared workspace
layer band, and -- at assistant-turn-start positions -- exact lens log-probs for
the A.21 readout + control vocabulary (the deterministic substrate the A.21
detector re-scores offline; C2 "the committed artifact is the auditable object").

Pinned tuple (C2/D4): {model_revision, lens_sha256, mapping_version}. Size is
bounded by (top_k x layers x positions) with a hard position cap and DISCLOSED
truncation (D5) -- never a silent drop.

Torch/transformers/jlens are imported lazily inside the live functions so this
file imports and self-tests on a GPU-less box. The pure builders + serialization
are exercised by --offline-selftest.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _wtcommon import (  # noqa: E402
    A21_READOUT_WORDS,
    A21_VOCAB_ID,
    KIT_VERSION,
    MAPPING_VERSION,
    SCHEMA_ID,
    BANNER,
    canonical_dumps,
    load_control_nouns,
    load_prompt_manifest,
    sha256_file,
    sha256_text,
    validate_artifact,
    write_canonical,
)

# Smoke defaults (D3: 4B-class is data-shape only, never a calibrated detector).
SMOKE_MODEL = "Qwen/Qwen3.5-4B"
DEFAULT_TOP_K = 16          # >=10 so the top-10 deception detector can run offline; power of two.
DEFAULT_BAND = "depth_fraction:0.40-0.70"
DEFAULT_POSITION_CAP = 1024


# ---------------------------------------------------------------------------
# Pure builders (no torch) -- exercised by --offline-selftest
# ---------------------------------------------------------------------------

def sort_readouts(readouts: Iterable[tuple[str, float]]) -> list[dict[str, Any]]:
    """Deterministic readout order: strength descending, then token ascending.

    The token tie-break makes the artifact byte-stable even when two readouts share
    a strength (a real occurrence at low precision) -- determinism tenet.
    """
    ordered = sorted(readouts, key=lambda tv: (-tv[1], tv[0]))
    return [{"token": t, "strength": v} for t, v in ordered]


def build_positions(raw_positions: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """Build the top-k ``positions`` array from raw per-position/per-layer readouts.

    ``raw_positions`` items: ``{index, role, token, layers: [{layer, topk: [(tok, lp)]}]}``.
    Readouts are sorted deterministically and truncated to ``top_k``. Positions are
    emitted in ascending index order.
    """
    out: list[dict[str, Any]] = []
    for pos in sorted(raw_positions, key=lambda p: p["index"]):
        frames = []
        for layer in sorted(pos["layers"], key=lambda f: f["layer"]):
            readouts = sort_readouts(layer["topk"])[:top_k]
            frames.append({"layer": layer["layer"], "readouts": readouts})
        out.append({
            "index": pos["index"],
            "role": pos["role"],
            "token": pos.get("token"),
            "frames": frames,
        })
    return out


def build_probe_readouts(
    raw_probe_positions: list[dict[str, Any]],
    control_set_sha256: str,
    control_set_size: int,
    readout_words: Iterable[str],
    dropped_multitoken: list[str],
) -> dict[str, Any]:
    """Assemble the ``probe_readouts`` block from raw per-position probe log-probs.

    ``raw_probe_positions`` items:
    ``{index, role, layers: [{layer, readout_logprobs: {word: lp}, control_logprobs: [lp|None]}]}``.
    ``control_logprobs`` arrays are aligned to the frozen control-noun file order;
    ``None`` marks a noun that did not resolve to a single token under this tokenizer.
    """
    positions = []
    for pos in sorted(raw_probe_positions, key=lambda p: p["index"]):
        layers = []
        for layer in sorted(pos["layers"], key=lambda f: f["layer"]):
            layers.append({
                "layer": layer["layer"],
                "readout_logprobs": dict(layer["readout_logprobs"]),
                "control_logprobs": list(layer["control_logprobs"]),
            })
        positions.append({"index": pos["index"], "role": pos["role"], "layers": layers})
    return {
        "vocab_id": A21_VOCAB_ID,
        "readout_tokens": list(readout_words),
        "control_set_sha256": control_set_sha256,
        "control_set_size": control_set_size,
        "positions": positions,
        "dropped_multitoken": sorted(dropped_multitoken),
    }


def apply_position_cap(
    raw_positions: list[dict[str, Any]], position_cap: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enforce the hard position cap; keep the earliest ``position_cap`` positions.

    Returns ``(kept, truncation_disclosure)``. Truncation is DISCLOSED (D5): if the
    cap bit, ``truncated`` is true and the counts show exactly what was retained.
    Dropping is deterministic (by ascending index), never silent.
    """
    ordered = sorted(raw_positions, key=lambda p: p["index"])
    kept = ordered[:position_cap]
    disclosure = {
        "position_cap": position_cap,
        "positions_total": len(ordered),
        "positions_captured": len(kept),
        "truncated": len(kept) < len(ordered),
    }
    return kept, disclosure


def assemble_artifact(
    *,
    pin: dict[str, Any],
    layer_band: dict[str, Any],
    top_k: int,
    prompt_set: dict[str, Any],
    positions: list[dict[str, Any]],
    probe_readouts: Optional[dict[str, Any]],
    truncation: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the full artifact object (time-free body)."""
    artifact: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "mapping_version": MAPPING_VERSION,
        "pin": pin,
        "layer_band": layer_band,
        "top_k": top_k,
        "prompt_set": prompt_set,
        "positions": positions,
        "truncation": truncation,
    }
    if probe_readouts is not None:
        artifact["probe_readouts"] = probe_readouts
    return artifact


def parse_layer_band(spec: str, num_model_layers: int) -> dict[str, Any]:
    """Resolve a layer-band spec to concrete, ascending, unique indices.

    Accepts ``explicit:12,13,14`` or ``depth_fraction:LO-HI`` (fractions of depth).
    No universal constant is baked in (C3 "no universal constants"): the depth
    fraction is a starting heuristic to be calibrated per model in P0, and the
    resolved indices + the rule used are RECORDED in the artifact.
    """
    if num_model_layers < 1:
        raise ValueError(f"num_model_layers must be >= 1, got {num_model_layers}")
    if spec.startswith("explicit:"):
        raw = [int(x) for x in spec[len("explicit:"):].split(",") if x.strip() != ""]
        layers = sorted(set(raw))
        if not layers:
            raise ValueError(f"explicit layer band {spec!r} resolved to no layers")
        if layers[-1] >= num_model_layers or layers[0] < 0:
            raise ValueError(
                f"explicit layer band {layers} out of range for {num_model_layers} layers"
            )
        return {"rule": "explicit", "num_model_layers": num_model_layers, "layers": layers}
    if spec.startswith("depth_fraction:"):
        body = spec[len("depth_fraction:"):]
        try:
            lo_s, hi_s = body.split("-")
            lo, hi = float(lo_s), float(hi_s)
        except ValueError as exc:
            raise ValueError(f"bad depth_fraction spec {spec!r}: {exc}") from exc
        if not (0.0 <= lo < hi <= 1.0):
            raise ValueError(f"depth_fraction must satisfy 0<=lo<hi<=1, got {lo}-{hi}")
        start = max(0, round(lo * num_model_layers))
        end = min(num_model_layers, round(hi * num_model_layers))
        layers = list(range(start, end))
        if not layers:
            raise ValueError(
                f"depth_fraction {spec!r} resolved to no layers for {num_model_layers} layers"
            )
        return {
            "rule": f"depth_fraction:{lo:.2f}-{hi:.2f}",
            "num_model_layers": num_model_layers,
            "layers": layers,
        }
    raise ValueError(
        f"unrecognized layer-band spec {spec!r}; use 'explicit:...' or 'depth_fraction:LO-HI'"
    )


def resolve_single_token(
    encode_fn: Callable[[str], list[int]], word: str
) -> Optional[tuple[int, str]]:
    """Resolve ``word`` to a single token id, trying a leading-space variant.

    Returns ``(token_id, resolved_form)`` or ``None`` when neither form is a single
    token under this tokenizer. This is the paper's single-token-vocabulary limit
    (workspace-observability-plan.md §5) made explicit and per-model: multi-token
    probe words are DROPPED and disclosed, never silently mis-measured. ``encode_fn``
    is injected (a thin wrapper over the tokenizer) so this stays unit-testable
    without transformers.
    """
    for form in (word, " " + word):
        ids = encode_fn(form)
        if len(ids) == 1:
            return ids[0], form
    return None


def resolve_probe_vocabulary(
    encode_fn: Callable[[str], list[int]],
    readout_words: Iterable[str],
    control_nouns: Iterable[str],
) -> tuple[dict[str, tuple[int, str]], list[Optional[tuple[int, str]]], list[str]]:
    """Resolve the full A.21 probe vocabulary to single tokens.

    Returns ``(readout_ids, control_ids, dropped)`` where ``readout_ids`` maps each
    resolvable readout word to ``(id, form)``, ``control_ids`` is aligned to the
    control-noun order (``None`` for unresolved nouns), and ``dropped`` lists every
    probe word that did not resolve (disclosure). Pure -- tested with a fake encoder.
    """
    readout_ids: dict[str, tuple[int, str]] = {}
    dropped: list[str] = []
    for w in readout_words:
        r = resolve_single_token(encode_fn, w)
        if r is None:
            dropped.append(w)
        else:
            readout_ids[w] = r
    control_ids: list[Optional[tuple[int, str]]] = []
    for n in control_nouns:
        r = resolve_single_token(encode_fn, n)
        if r is None:
            dropped.append(n)
        control_ids.append(r)
    return readout_ids, control_ids, dropped


# ---------------------------------------------------------------------------
# Sidecar (the ONLY place wall-clock time is allowed; body stays time-free)
# ---------------------------------------------------------------------------

def build_sidecar(*, artifact_sha256: str, captured_at: str, run_mode: str,
                  extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Build the run-metadata sidecar.

    ``captured_at`` (wall clock) is injected by the caller -- it is the single
    designated non-deterministic seam and it lives ONLY here, never in the artifact
    body, so the body remains byte-stable and auditable (C2).
    """
    sidecar = {
        "tool": "capture_workspace.py",
        "kit_version": KIT_VERSION,
        "artifact_sha256": artifact_sha256,
        "captured_at": captured_at,
        "run_mode": run_mode,
    }
    if extra:
        sidecar["host"] = extra
    return sidecar


# ---------------------------------------------------------------------------
# Live / GPU paths (UNTESTED HERE -- lazy imports, defensive against API drift)
# ---------------------------------------------------------------------------

def _load_model_and_tokenizer(model_id: str, revision: Optional[str]):
    """Load an HF model + tokenizer. UNTESTED: imports transformers/torch lazily."""
    try:
        import torch  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - GPU box only
        raise RuntimeError(
            "capture_workspace live mode needs torch + transformers. Install on the "
            "GPU box: pip install -r scripts/workspace-pilot/requirements.txt"
        ) from exc
    tok = AutoTokenizer.from_pretrained(model_id, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, revision=revision, torch_dtype="auto", device_map="auto"
    )
    model.eval()
    return model, tok


def _load_or_fit_lens(model, tok, lens_path: Optional[str], fit: bool):
    """Load a pre-fitted Jacobian lens, or fit one. UNTESTED: imports jlens lazily.

    Defensive against minor jlens API drift: tries ``JacobianLens.from_pretrained``
    for a pinned artifact and ``jlens.from_hf`` for the fit path, and reports the
    exact attribute it could not find rather than an opaque AttributeError.
    """
    try:
        import jlens  # type: ignore
    except ImportError as exc:  # pragma: no cover - GPU box only
        raise RuntimeError(
            "capture_workspace live mode needs the jlens package. Install on the GPU "
            "box: pip install git+https://github.com/anthropics/jacobian-lens"
        ) from exc
    if lens_path:
        loader = getattr(getattr(jlens, "JacobianLens", jlens), "from_pretrained", None)
        if loader is None:
            raise RuntimeError(
                "jlens exposes neither JacobianLens.from_pretrained nor from_pretrained; "
                "the installed jlens API differs from the one this kit targets -- pin the "
                "revision documented in requirements.txt and re-check the API."
            )
        return loader(lens_path)
    if fit:
        from_hf = getattr(jlens, "from_hf", None)
        if from_hf is None:
            raise RuntimeError("jlens.from_hf not found; cannot fit a lens (see requirements.txt pin)")
        return from_hf(model, tok)
    raise RuntimeError("provide --lens-path <pretrained> or --fit-lens (see README fit-or-download step)")


def _lens_apply(lens, model, prompt: str, positions):
    """Call ``lens.apply`` defensively. UNTESTED. Returns the lens's per-position result."""
    apply = getattr(lens, "apply", None)
    if apply is None:
        raise RuntimeError("lens object has no .apply(model, prompt, positions=...) method (API drift)")
    return apply(model, prompt, positions=positions)


# ---------------------------------------------------------------------------
# Offline self-test (stdlib only, no torch) -- this is the local test tier
# ---------------------------------------------------------------------------

def offline_selftest() -> int:
    """Exercise every non-GPU path on committed sample data. Returns 0 on success."""
    print(BANNER)
    print("[capture] --offline-selftest: exercising pure builders + serialization")
    here = Path(__file__).resolve().parent

    # 1. Layer-band resolution (both rules, and out-of-range refusal).
    band = parse_layer_band("depth_fraction:0.40-0.70", 28)
    assert band["layers"] == list(range(11, 20)), band
    band_x = parse_layer_band("explicit:4,5", 8)
    assert band_x == {"rule": "explicit", "num_model_layers": 8, "layers": [4, 5]}, band_x
    for bad in ("explicit:9", "depth_fraction:0.7-0.4", "garbage"):
        try:
            parse_layer_band(bad, 8)
        except ValueError:
            pass
        else:
            raise AssertionError(f"parse_layer_band accepted bad spec {bad!r}")
    print("  layer-band resolution: OK")

    # 2. Probe-vocabulary resolution with a FAKE tokenizer (single-token discipline).
    #    "fictional" is single-token; "simulation" only as a leading-space variant;
    #    "compasses" is multi-token -> dropped and disclosed.
    vocab = {"fictional": [1], " fictional": [1], "simulation": [2, 9],
             " simulation": [7], "apple": [3], " apple": [3], "compasses": [4, 5]}
    encode = lambda t: vocab.get(t, [0, 0])  # noqa: E731 - unknown -> multi-token
    readout_ids, control_ids, dropped = resolve_probe_vocabulary(
        encode, ("fictional", "simulation"), ("apple", "compasses"))
    assert readout_ids["fictional"] == (1, "fictional"), readout_ids
    assert readout_ids["simulation"] == (7, " simulation"), readout_ids
    assert control_ids[0] == (3, "apple") and control_ids[1] is None, control_ids
    assert dropped == ["compasses"], dropped
    print("  probe-vocab resolution + multitoken disclosure: OK")

    # 3. Build positions from raw frames; readouts sort deterministically + truncate.
    raw_pos = [
        {"index": 1, "role": "prompt", "token": "b",
         "layers": [{"layer": 5, "topk": [("x", -3.0), ("y", -1.0), ("z", -1.0)]}]},
        {"index": 0, "role": "prompt", "token": "a",
         "layers": [{"layer": 4, "topk": [("p", -2.0), ("q", -0.5)]}]},
    ]
    positions = build_positions(raw_pos, top_k=2)
    assert [p["index"] for p in positions] == [0, 1], "positions must be index-ordered"
    # strength desc then token asc: -1.0 ties break y<z; then -3.0 dropped by top_k=2
    assert positions[1]["frames"][0]["readouts"] == [
        {"token": "y", "strength": -1.0}, {"token": "z", "strength": -1.0}], positions[1]
    print("  top-k build (deterministic sort + truncate): OK")

    # 4. Position cap discloses truncation, never silent.
    kept, disc = apply_position_cap(raw_pos, position_cap=1)
    assert disc == {"position_cap": 1, "positions_total": 2,
                    "positions_captured": 1, "truncated": True}, disc
    _, disc2 = apply_position_cap(raw_pos, position_cap=10)
    assert disc2["truncated"] is False, disc2
    print("  position-cap truncation disclosure: OK")

    # 5. Assemble the committed sample artifacts, re-serialize, assert byte-idempotent
    #    round-trip and schema validity (jsonschema if present, else structural).
    import json as _json
    method_seen = set()
    for name in ("sample_eval_trajectory.json", "sample_deploy_trajectory.json"):
        path = here / "sample_data" / name
        if not path.exists():
            raise AssertionError(f"missing committed sample artifact {path}; run make_samples.py")
        text = path.read_text(encoding="utf-8")
        obj = _json.loads(text)
        assert canonical_dumps(obj) == text, f"{name} is not canonically serialized"
        ok, errs, method = validate_artifact(obj, here / "schema" / "workspace_trajectory.draft.schema.json")
        method_seen.add(method)
        assert ok, f"{name} failed {method} validation: {errs}"
        # body must be time-free
        assert "captured_at" not in text and "timestamp" not in text, f"{name} body carries a timestamp"
    print(f"  sample-artifact round-trip + validation ({', '.join(sorted(method_seen))}): OK")

    # 6. Prompt manifest self-consistency (committed shas match the payload bytes).
    manifest_path = here / "prompts" / "manifest.json"
    if manifest_path.exists():
        manifest = load_prompt_manifest(manifest_path)
        assert manifest["prompts"], "prompt manifest is empty"
        print(f"  prompt-manifest sha verification ({len(manifest['prompts'])} prompts): OK")
    else:
        print("  prompt-manifest: SKIP (not generated yet)")

    # 7. Sidecar keeps time out of the body.
    sc = build_sidecar(artifact_sha256="deadbeef", captured_at="2026-07-07T00:00:00Z", run_mode="selftest")
    assert sc["captured_at"] == "2026-07-07T00:00:00Z" and sc["artifact_sha256"] == "deadbeef"
    print("  sidecar shape: OK")

    print("[capture] --offline-selftest PASSED")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_live(args: argparse.Namespace) -> int:  # pragma: no cover - GPU box only
    """UNTESTED live capture. Guarded, defensive, disclosed. Runs only on a GPU box."""
    import datetime

    model_id = SMOKE_MODEL if args.smoke else args.model
    if not model_id:
        raise RuntimeError("provide --model <hf-id> (or --smoke for the 4B data-shape default)")
    if args.smoke:
        print(BANNER)
        print(f"[capture] --smoke: {SMOKE_MODEL} is DATA-SHAPE ONLY (plan D3); not a calibrated run.")
    revision = args.model_revision
    if not revision and not args.allow_unpinned:
        raise RuntimeError(
            "refusing to run without --model-revision (pinning discipline, D4). "
            "Pass the exact HF revision, or --allow-unpinned to bypass with disclosure."
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    control_nouns = load_control_nouns(args.control_nouns)
    control_sha = sha256_file(args.control_nouns) if args.control_nouns else sha256_file(
        Path(__file__).resolve().parent / "data" / "control_nouns.txt")

    manifest = load_prompt_manifest(args.prompts)
    model, tok = _load_model_and_tokenizer(model_id, revision)
    lens = _load_or_fit_lens(model, tok, args.lens_path, args.fit_lens)

    lens_sha = sha256_file(args.lens_path) if args.lens_path else f"UNPINNED-fitted-in-session/{KIT_VERSION}"
    model_rev = f"{model_id}@{revision}" if revision else f"{model_id}@UNPINNED"
    num_layers = int(getattr(getattr(model, "config", object()), "num_hidden_layers", 0) or 0)
    if num_layers < 1:
        raise RuntimeError("could not read model.config.num_hidden_layers (API drift); cannot resolve layer band")
    layer_band = parse_layer_band(args.layer_band, num_layers)

    encode = lambda t: tok.encode(t, add_special_tokens=False)  # noqa: E731
    readout_ids, control_ids, dropped = resolve_probe_vocabulary(
        encode, A21_READOUT_WORDS, control_nouns)

    written = 0
    for entry in manifest["prompts"]:
        prompt_path = Path(args.prompts).resolve().parent / entry["path"]
        prompt_text = prompt_path.read_text(encoding="utf-8")
        # NOTE (UNTESTED): the raw-frame extraction from _lens_apply is model/lens
        # specific and must be adapted to the installed jlens result shape. It must
        # produce raw_positions and raw_probe_positions in the shapes the pure
        # builders above consume. Kept explicit so the failure is loud, not silent.
        raw_positions, raw_probe_positions = _extract_raw(
            lens, model, tok, prompt_text, layer_band["layers"],
            args.top_k, readout_ids, control_ids)
        kept, truncation = apply_position_cap(raw_positions, args.position_cap)
        positions = build_positions(kept, args.top_k)
        probe = build_probe_readouts(
            raw_probe_positions, control_sha, len(control_nouns), A21_READOUT_WORDS, dropped)
        pin = {"model_revision": model_rev, "lens_sha256": lens_sha, "mapping_version": MAPPING_VERSION}
        prompt_set = {
            "manifest_sha256": manifest["manifest_sha256"],
            "prompt_id": entry["id"],
            "prompt_class": entry["class"],
            "prompt_sha256": entry["sha256"],
        }
        artifact = assemble_artifact(
            pin=pin, layer_band=layer_band, top_k=args.top_k, prompt_set=prompt_set,
            positions=positions, probe_readouts=probe, truncation=truncation)
        ok, errs, method = validate_artifact(artifact)
        if not ok:
            raise RuntimeError(f"captured artifact for {entry['id']} failed {method} validation: {errs}")
        if method != "jsonschema":
            print(f"  WARNING: jsonschema not installed; {entry['id']} passed structural fallback only")
        art_path = out_dir / f"{entry['id']}.workspace_trajectory.json"
        art_sha = write_canonical(art_path, artifact)
        captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_canonical(out_dir / f"{entry['id']}.workspace_trajectory.sidecar.json",
                        build_sidecar(artifact_sha256=art_sha, captured_at=captured_at,
                                      run_mode="smoke" if args.smoke else "live",
                                      extra={"model": model_id, "revision": revision or "UNPINNED"}))
        written += 1
        print(f"  wrote {art_path.name} (sha256 {art_sha[:12]}..)")
    print(f"[capture] wrote {written} artifact(s) to {out_dir}")
    return 0


def _extract_raw(lens, model, tok, prompt, layers, top_k, readout_ids, control_ids):  # pragma: no cover
    """UNTESTED adapter: turn a jlens apply() result into the pure-builder raw shapes.

    This is the single point that depends on the installed jlens result layout. It
    is intentionally a thin, loud shim: adapt it to the real API on the GPU box and
    verify against --smoke output before trusting a >=27B run. Raises rather than
    guessing if the result shape is not what this kit targets.
    """
    result = _lens_apply(lens, model, prompt, positions="all")
    raise RuntimeError(
        "UNTESTED: adapt _extract_raw to the installed jlens result shape "
        f"(got {type(result).__name__}). It must return (raw_positions, raw_probe_positions) "
        "in the shapes build_positions / build_probe_readouts consume; see the schema draft doc. "
        "This guard prevents a silently wrong artifact -- fill it in on the GPU box."
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Capture candidate workspace_trajectory.json artifacts (UNTESTED ON GPU kit).")
    p.add_argument("--offline-selftest", action="store_true",
                   help="Run the stdlib self-test (no torch); the local test tier.")
    p.add_argument("--smoke", action="store_true",
                   help=f"Live smoke run against {SMOKE_MODEL} (data-shape only, D3).")
    p.add_argument("--model", default=None, help="HF model id for a live run.")
    p.add_argument("--model-revision", default=None, help="Exact HF revision to pin (D4).")
    p.add_argument("--allow-unpinned", action="store_true",
                   help="Bypass the revision-pin requirement, recording UNPINNED (disclosed).")
    p.add_argument("--lens-path", default=None, help="Pre-fitted lens artifact (from_pretrained).")
    p.add_argument("--fit-lens", action="store_true", help="Fit a lens in-session via jlens.from_hf.")
    p.add_argument("--prompts", default=str(Path(__file__).resolve().parent / "prompts" / "manifest.json"),
                   help="Prompt-set manifest.json.")
    p.add_argument("--control-nouns", default=None,
                   help="Control-noun file (defaults to the frozen data/control_nouns.txt).")
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Top-k readouts per frame.")
    p.add_argument("--layer-band", default=DEFAULT_BAND,
                   help="Layer-band spec: 'explicit:12,13' or 'depth_fraction:LO-HI'.")
    p.add_argument("--position-cap", type=int, default=DEFAULT_POSITION_CAP,
                   help="Hard cap on captured positions (truncation disclosed).")
    p.add_argument("--out", default="artifacts", help="Output directory for artifacts + sidecars.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.offline_selftest:
        return offline_selftest()
    if args.smoke or args.model or args.lens_path or args.fit_lens:
        return _run_live(args)
    print(BANNER)
    print("Nothing to do. Use --offline-selftest (no GPU) or --smoke/--model (GPU box).")
    print("See scripts/workspace-pilot/README.md for the runbook.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
