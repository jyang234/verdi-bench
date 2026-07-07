# ============================================================================
# UNTESTED ON GPU -- workspace-pilot kit. Exercise with --smoke first.
# This module has stdlib-only paths that ARE exercised here via each script's
# --offline-selftest. It imports no torch/transformers/jlens and nothing from
# harness/. See scripts/workspace-pilot/README.md "Untested surfaces".
# ============================================================================
"""Shared, deterministic IO + hashing + schema helpers for the workspace pilot.

Single source of truth (so the *writer* `capture_workspace.py` and the *reader*
`score_eval_awareness.py` cannot drift) for:

* the frozen mapping version and the A.21 readout-token set
  (workspace-observability-plan.md C2 / C3 / D2);
* deterministic JSON serialization (sorted keys, no wall-clock in the body);
* the frozen control-noun loader (comment-skipping, dedup-checked);
* sha256 helpers and the pinned-tuple / prompt-set provenance shapes;
* artifact validation that uses ``jsonschema`` when importable and a loud
  structural fallback when it is not (C2 "the committed artifact is the
  auditable object").

This directory is standalone: it is not part of the ``verdi-bench`` uv project,
imports nothing from ``harness/``, and nothing under ``tests/`` imports it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# --- frozen constants (pinned with mapping_version; see the schema draft doc) ---

MAPPING_VERSION = "wt/v1-draft"
SCHEMA_ID = "workspace_trajectory.draft"
KIT_VERSION = "wt-pilot/0.1-draft"

# A.21 readout tokens, verbatim from the plan (C3 first detector). Frozen with the
# mapping version. Capture also tries a leading-space variant per tokenizer; the
# *resolved* form and its log-prob are what land in the artifact.
A21_READOUT_WORDS = ("fictional", "simulation")
A21_VOCAB_ID = "a21/v0"

# Relative (to this directory) locations of the committed data + schema.
CONTROL_NOUNS_REL = "data/control_nouns.txt"
SCHEMA_REL = "schema/workspace_trajectory.draft.schema.json"

HERE = Path(__file__).resolve().parent


# --- hashing --------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    """Hex sha256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex sha256 of text, hashed as UTF-8 (the byte form we commit)."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    """Hex sha256 of a file's bytes (byte-exact, no newline normalization)."""
    return sha256_bytes(Path(path).read_bytes())


# --- deterministic serialization ------------------------------------------------

def canonical_dumps(obj: Any) -> str:
    """Serialize ``obj`` to a byte-stable JSON string.

    Sorted keys, ASCII-escaped, 2-space indent, trailing newline. Determinism is
    the point (C2): the same object always yields the same bytes, so re-serializing
    a loaded artifact is idempotent. Callers MUST keep wall-clock time out of the
    artifact body -- ``captured_at`` lives only in the sidecar.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, indent=2) + "\n"


def write_canonical(path: str | Path, obj: Any) -> str:
    """Write ``obj`` canonically to ``path``; return the file's sha256."""
    text = canonical_dumps(obj)
    Path(path).write_text(text, encoding="utf-8")
    return sha256_text(text)


# --- frozen control-noun set ----------------------------------------------------

def load_control_nouns(path: str | Path | None = None) -> list[str]:
    """Load the frozen control-noun list in file order.

    Blank lines and ``#`` comment lines are skipped (so the data file can carry the
    UNTESTED banner without polluting the set). Order is load-bearing: an artifact's
    ``control_logprobs`` array aligns to this order, and ``control_set_sha256`` pins
    the *bytes*. Duplicates fail loudly -- a silently doubled noun would skew the
    control mean and thus every A.21 score.
    """
    p = Path(path) if path is not None else (HERE / CONTROL_NOUNS_REL)
    nouns: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        nouns.append(line)
    if not nouns:
        raise ValueError(f"control-noun file {p} is empty after comment/blank stripping")
    dupes = sorted({n for n in nouns if nouns.count(n) > 1})
    if dupes:
        raise ValueError(f"control-noun file {p} has duplicate nouns: {dupes}")
    return nouns


# --- prompt-set manifest --------------------------------------------------------

def load_prompt_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Load + verify a prompt manifest, failing loudly on sha drift.

    Each entry's committed ``sha256`` is recomputed from the referenced file (paths
    are relative to the manifest's directory). A mismatch means a prompt payload was
    edited without regenerating the manifest -- the exact silent drift that would let
    the model see different bytes than the artifact's provenance claims. We refuse it.
    Returns the parsed manifest dict (with a computed ``manifest_sha256``).
    """
    mp = Path(manifest_path).resolve()
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    base = mp.parent
    mismatches: list[str] = []
    for entry in manifest.get("prompts", []):
        fp = base / entry["path"]
        if not fp.exists():
            mismatches.append(f"{entry['id']}: missing file {entry['path']}")
            continue
        actual = sha256_file(fp)
        if actual != entry["sha256"]:
            mismatches.append(
                f"{entry['id']}: sha256 drift (manifest {entry['sha256'][:12]}.. "
                f"!= file {actual[:12]}..)"
            )
    if mismatches:
        raise ValueError(
            "prompt manifest is out of date -- regenerate with make_manifest.py:\n  - "
            + "\n  - ".join(mismatches)
        )
    manifest["manifest_sha256"] = sha256_file(mp)
    return manifest


# --- artifact validation --------------------------------------------------------

def load_schema(schema_path: str | Path | None = None) -> dict[str, Any]:
    """Load the committed draft JSON Schema (as a dict)."""
    p = Path(schema_path) if schema_path is not None else (HERE / SCHEMA_REL)
    return json.loads(p.read_text(encoding="utf-8"))


def _structural_errors(obj: Any) -> list[str]:
    """A loud, minimal structural check used when ``jsonschema`` is not installed.

    This is deliberately NOT a JSON Schema engine -- it only asserts the load-bearing
    invariants (pinned tuple present, mapping version frozen, deterministic
    containers, truncation disclosed as booleans). Full validation runs only where
    ``jsonschema`` is importable (the GPU box, or a dev box that pip-installs it);
    the README lists this as an untested surface here.
    """
    errs: list[str] = []
    if not isinstance(obj, dict):
        return [f"artifact must be a JSON object, got {type(obj).__name__}"]
    for key in ("schema", "mapping_version", "pin", "layer_band", "top_k",
                "prompt_set", "positions", "truncation"):
        if key not in obj:
            errs.append(f"missing required top-level key: {key}")
    if obj.get("mapping_version") != MAPPING_VERSION:
        errs.append(f"mapping_version must be {MAPPING_VERSION!r}, got {obj.get('mapping_version')!r}")
    pin = obj.get("pin")
    if not isinstance(pin, dict):
        errs.append("pin must be an object")
    else:
        for key in ("model_revision", "lens_sha256", "mapping_version"):
            if key not in pin:
                errs.append(f"pin missing required key: {key}")
    if not isinstance(obj.get("positions"), list):
        errs.append("positions must be an array")
    trunc = obj.get("truncation")
    if not isinstance(trunc, dict):
        errs.append("truncation must be an object")
    elif not isinstance(trunc.get("truncated"), bool):
        errs.append("truncation.truncated must be a boolean (never silently dropped)")
    return errs


def validate_artifact(obj: Any, schema_path: str | Path | None = None) -> tuple[bool, list[str], str]:
    """Validate an artifact object; return ``(ok, errors, method)``.

    ``method`` is ``"jsonschema"`` when the library was importable (full validation)
    or ``"structural-fallback"`` when it was not. Callers WARN-and-continue on the
    fallback rather than claiming a validation they did not perform.
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return (len(_structural_errors(obj)) == 0, _structural_errors(obj), "structural-fallback")
    schema = load_schema(schema_path)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errs = [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(obj)]
    return (len(errs) == 0, errs, "jsonschema")


BANNER = (
    "UNTESTED ON GPU -- workspace-pilot kit. This kit has never run against a real "
    "model/lens; exercise --smoke on a small model before the >=27B primary run."
)
