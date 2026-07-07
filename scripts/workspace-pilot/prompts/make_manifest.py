# ============================================================================
# UNTESTED ON GPU -- workspace-pilot kit. Exercise with --smoke first.
# Stdlib-only; runs here. Regenerates the prompt manifest from the committed
# payloads so the recorded shas cannot silently drift from the bytes the model
# sees. Run after editing any prompt:
#   python3 scripts/workspace-pilot/prompts/make_manifest.py
# ============================================================================
"""Generate prompts/manifest.json from the committed prompt payloads.

Prompt CLASS and pairing are derived from the directory + filename so metadata
cannot disagree with the file it describes:

* ``corpus/<id>.md``           -> class ``corpus-task`` (verbatim copies; see PROVENANCE.md)
* ``realism_pairs/<pair>_eval.md`` -> class ``realism-eval``, ``pair: <pair>``
* ``realism_pairs/<pair>_real.md`` -> class ``realism-real``, ``pair: <pair>``
* ``controls/<id>.md``         -> class ``deployment-control``

Output is deterministic (sorted by id, key-sorted JSON, no timestamps). The A.21
calibration expectation (plan §5 / C4): realism-eval prompts should score HIGHER
than their realism-real partner; deployment-control prompts should not flag.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _wtcommon import MAPPING_VERSION, canonical_dumps, sha256_file  # noqa: E402

# Provenance for the verbatim corpus copies (cite task ids, plan §5 / A3).
CORPUS_SOURCE = "verdi-bench-corpus:corpora/groundwork-v0/tasks/{id}/prompt.md"
CORPUS_CLASS = {"gw-r1": "reach-trap", "gw-o1": "obligation-trap",
                "gw-n1": "null", "gw-m1": "multi-impl"}


def _entry_for(path: Path) -> dict:
    rel = path.relative_to(HERE).as_posix()
    stem = path.stem
    parent = path.parent.name
    entry: dict = {"id": stem, "path": rel, "sha256": sha256_file(path)}
    if parent == "corpus":
        entry["class"] = "corpus-task"
        entry["source"] = CORPUS_SOURCE.format(id=stem)
        entry["corpus_class"] = CORPUS_CLASS.get(stem, "unknown")
    elif parent == "realism_pairs":
        pair, _, variant = stem.partition("_")
        entry["class"] = f"realism-{variant}"
        entry["pair"] = pair
    elif parent == "controls":
        entry["class"] = "deployment-control"
    else:
        raise ValueError(f"prompt in unexpected dir: {path}")
    return entry


def build_manifest() -> dict:
    prompts = []
    for sub in ("corpus", "realism_pairs", "controls"):
        for path in sorted((HERE / sub).glob("*.md")):
            if path.name.upper().startswith("PROVENANCE") or path.name.upper().startswith("README"):
                continue
            prompts.append(_entry_for(path))
    prompts.sort(key=lambda e: e["id"])
    return {
        "mapping_version": MAPPING_VERSION,
        "note": ("Pilot prompt set (workspace-observability-plan.md P0). corpus-task "
                 "prompts are verbatim copies (see PROVENANCE.md); realism-* pairs and "
                 "deployment-control prompts are authored fresh for A.21 calibration."),
        "prompts": prompts,
    }


def main() -> int:
    manifest = build_manifest()
    out = HERE / "manifest.json"
    out.write_text(canonical_dumps(manifest), encoding="utf-8")
    print(f"wrote {out} with {len(manifest['prompts'])} prompts")
    for e in manifest["prompts"]:
        extra = f" pair={e['pair']}" if "pair" in e else ""
        print(f"  {e['id']:12s} {e['class']:18s}{extra}  {e['sha256'][:12]}..")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
