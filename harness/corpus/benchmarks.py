"""Importers for recognized public benchmark datasets [EVAL-8 §M1, D001].

verdi-bench is an *instrument*, not a *benchmark* — it runs a corpus, it does not
author one. This module is how a user points the instrument at a **standardized,
citable task set** (the SWE-bench family first) instead of hand-writing tasks, so
a verdi finding can cite external, community-scrutinized task content rather than
a private one-off corpus.

Each importer is a :class:`~harness.corpus.public.TaskSource` that maps a
benchmark's *native* record schema onto verdi's Harbor task content, and rides
the existing idempotent-import / manifest / admission machinery
(:func:`harness.corpus.public.import_public_dataset`). The mapping is a pure
function of an offline record — the network fetch (exporting the dataset) is the
user's one-time step, kept out of this module so it stays deterministic and
offline-testable, exactly like :class:`~harness.corpus.public.DirectorySource`.

**Insulation by construction.** A benchmark like SWE-bench ships the grading
tests (its ``test_patch`` + ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` lists) alongside
the problem statement. The mapping separates them: the agent-visible portion is
the problem statement only, and the grading portion lands under a nested
``holdout`` key that :func:`harness.corpus.materialize.materialize_experiment`
writes into the read-only holdouts directory — never into the trial workspace
[EVAL-4 AC-9]. The two halves cannot leak into each other because they travel in
different keys and the materializer routes them to different files.

**Task identity vs deployment wiring.** The citable content sha covers a task's
*intrinsic* identity (problem, tests, repo, base commit, version) — not the
per-instance container image ref, which is deployment wiring (a registry mirror,
a digest re-pin) the same way the proxy and provider keys are operational, never
pre-registered. The image ref rides the manifest entry's ``metadata`` and is
materialized into ``tasks.yaml``, so re-pinning an image digest does not churn
the corpus identity a finding cites.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from .public import TERMINAL_BENCH, DirectorySource, RawTask, TaskSource

SWEBENCH = "swe-bench"

# SWE-bench publishes one pre-built image per instance. The dataset carries a
# tag, not a digest; verdi runs `--pull=never` on digest-pinned images, so the
# user re-pins the digest at materialize time (documented in the usage guide).
# {instance_id} and {repo} are the available placeholders.
DEFAULT_SWEBENCH_IMAGE_TEMPLATE = (
    "docker.io/swebench/sweb.eval.x86_64.{instance_id}:latest"
)

# The intrinsic-identity fields copied verbatim from a SWE-bench record into the
# grading holdout. Kept a closed set so an unexpected schema drift is visible.
_SWEBENCH_HOLDOUT_FIELDS = (
    "test_patch",
    "repo",
    "base_commit",
    "version",
    "environment_setup_commit",
)


class BenchmarkRecordError(ValueError):
    """A public-benchmark record is missing a field the mapping requires.

    Fail loud [master plan §7.7]: a record we cannot map into a well-formed,
    gradable verdi task is refused with the offending field named, never
    silently imported as a half-task that fails opaquely mid-run.
    """


def _require(record: dict, field: str, instance: str) -> object:
    if field not in record or record[field] in (None, ""):
        raise BenchmarkRecordError(
            f"SWE-bench record {instance!r} is missing required field {field!r}"
        )
    return record[field]


def _as_test_list(value: object, field: str, instance: str) -> list[str]:
    """SWE-bench ships FAIL_TO_PASS / PASS_TO_PASS as either a JSON-encoded string
    (the HF dataset) or a real list. Normalize to a list of test ids; anything
    else is refused loudly."""
    if value in (None, ""):
        return []  # absent / empty is "no tests here", not a parse error
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            raise BenchmarkRecordError(
                f"SWE-bench record {instance!r} field {field!r} is a string but not "
                f"JSON: {e}"
            ) from e
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise BenchmarkRecordError(
            f"SWE-bench record {instance!r} field {field!r} must be a list of test "
            f"ids (or a JSON-encoded one), got {type(value).__name__}"
        )
    return value


def swebench_task_content(record: dict) -> dict:
    """Map one SWE-bench instance record → Harbor task content (pure).

    The returned dict is the task's *citable* content — its content sha is the
    identity a finding cites. It separates the agent-visible problem statement
    from the grading holdout so :func:`materialize_experiment` can route them to
    the workspace and the read-only holdouts dir respectively.
    """
    raw_id = record.get("instance_id") or record.get("id")
    if not raw_id or not str(raw_id).strip():
        raise BenchmarkRecordError(
            "SWE-bench record has no 'instance_id' (or 'id'); the task would have "
            "no citable identity — refusing rather than importing it as '<unknown>'"
        )
    instance = str(raw_id)
    problem = _require(record, "problem_statement", instance)
    fail_to_pass = _as_test_list(_require(record, "FAIL_TO_PASS", instance), "FAIL_TO_PASS", instance)
    pass_to_pass = _as_test_list(record.get("PASS_TO_PASS", []), "PASS_TO_PASS", instance)

    holdout = {f: record[f] for f in _SWEBENCH_HOLDOUT_FIELDS if record.get(f) not in (None, "")}
    holdout["kind"] = SWEBENCH
    holdout["fail_to_pass"] = fail_to_pass
    holdout["pass_to_pass"] = pass_to_pass

    return {
        "id": instance,
        "prompt": str(problem),
        # grading material — never agent-visible; the materializer writes it to
        # the read-only holdouts dir, not tasks.yaml.
        "holdout": holdout,
    }


def swebench_holdout_results(holdout_spec: dict, test_outcomes: dict) -> dict:
    """The ``holdout_results.json`` a SWE-bench grading image emits [reference].

    This is the grading-image contract expressed as a pure function: the image
    applies the recorded ``test_patch`` and runs the recorded tests inside the
    instance container; this shapes the per-test outcomes into the
    ``{"assertions": [...]}`` document the deterministic grader parses
    (:func:`harness.grade.deterministic.parse_holdout_output`).

    ``holdout_spec`` is the materialized ``holdout.json``; ``test_outcomes`` is
    ``{test_id: passed_bool}`` from actually running the tests. SWE-bench
    "resolved" == every ``FAIL_TO_PASS`` and ``PASS_TO_PASS`` test passes, which
    is exactly what ``compute_binary_score`` (all holdout assertions pass) then
    computes. A spec test absent from the outcomes is ``fail`` — a test that did
    not run did not pass — never silently dropped [master plan §7.7].
    """
    ids = list(dict.fromkeys(
        [*holdout_spec.get("fail_to_pass", []), *holdout_spec.get("pass_to_pass", [])]
    ))
    if not ids:
        raise BenchmarkRecordError("SWE-bench holdout spec names no tests to grade")
    return {
        "assertions": [
            {"id": tid, "result": "pass" if test_outcomes.get(tid, False) else "fail"}
            for tid in ids
        ]
    }


def swebench_task_metadata(record: dict, *, image_template: str) -> dict:
    """Deployment wiring + stratification metadata for a SWE-bench record.

    Kept OUT of the citable content: the image ref is a mirror/digest that a user
    re-pins without changing the task's identity, and ``created_at`` feeds the
    contamination sentinel's cutoff dating [EVAL-10 AC-1] rather than the sha.
    """
    instance = str(record.get("instance_id") or record.get("id") or "<unknown>")
    repo = str(record.get("repo") or "")
    try:
        image = image_template.format(instance_id=instance, repo=repo.replace("/", "__"))
    except (KeyError, IndexError) as e:
        raise BenchmarkRecordError(
            f"image template {image_template!r} references an unknown placeholder "
            f"{e}; only {{instance_id}} and {{repo}} are available"
        ) from e
    meta: dict = {
        "image": image,
        "repo": repo,
        "version": record.get("version"),
        # stratify by repo by default — a natural, dataset-provided stratum.
        "category": repo or "unknown",
    }
    created_at = record.get("created_at")
    if created_at:
        # RFC 3339 date the task's source material entered the world → the
        # dating channel reads it as `created_at` on the manifest entry.
        meta["created_at"] = str(created_at)
    return meta


def _read_records(instances_path: Path) -> Iterator[dict]:
    """Yield instance records from a JSON array file or a JSONL file.

    Both are how `datasets.load_dataset(...).to_json(...)` / `to_pandas(...)`
    export SWE-bench; supporting either keeps the user's export step trivial.
    """
    text = instances_path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise BenchmarkRecordError(
                f"{instances_path}: top-level JSON must be an array of records"
            )
        for i, rec in enumerate(data):
            if not isinstance(rec, dict):
                raise BenchmarkRecordError(
                    f"{instances_path}: array element {i} is not a JSON object"
                )
            yield rec
        return
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise BenchmarkRecordError(f"{instances_path} line {i + 1}: not JSON: {e}") from e
        if not isinstance(rec, dict):
            raise BenchmarkRecordError(f"{instances_path} line {i + 1}: not a JSON object")
        yield rec


class SweBenchSource:
    """A :class:`TaskSource` over a locally-exported SWE-bench instances file.

    The user exports the dataset once (``datasets.load_dataset(
    'princeton-nlp/SWE-bench_Verified', split='test').to_json('instances.jsonl')``)
    and points the importer at that file — no network inside the harness, so the
    import stays deterministic and offline-testable.
    """

    def __init__(self, instances_path, *, image_template: Optional[str] = None) -> None:
        self.instances_path = Path(instances_path)
        self.image_template = image_template or DEFAULT_SWEBENCH_IMAGE_TEMPLATE

    def fetch(self) -> list[RawTask]:
        out: list[RawTask] = []
        seen: set[str] = set()
        for record in _read_records(self.instances_path):
            content = swebench_task_content(record)
            task_id = content["id"]
            if task_id in seen:
                raise BenchmarkRecordError(
                    f"SWE-bench export has a duplicate instance_id {task_id!r}"
                )
            seen.add(task_id)
            out.append(
                RawTask(
                    task_id=task_id,
                    content=content,
                    metadata=swebench_task_metadata(record, image_template=self.image_template),
                )
            )
        return out


# --- the importer registry [refactor 07 §3] --------------------------------
@dataclass(frozen=True)
class ImporterSpec:
    """One recognized public-benchmark importer [refactor 07 §3].

    A ``TaskSource`` factory over a locally-exported record file plus the default
    dataset name/version recorded on the manifest/card. ``--benchmark`` validates
    against :data:`IMPORTERS` and derives its help text (``description``) and its
    refusal message from the registry, so the valid set is defined in exactly one
    place: a new importer is a ``TaskSource`` class + a registry entry, and the
    generic idempotent engine (:func:`harness.corpus.public.import_public_dataset`)
    does the rest.

    ``corpus_id_names_dataset``: a generic directory has no canonical dataset
    name, so ``--corpus-id`` names it; a benchmark with a canonical identity
    (swebench) keeps ``dataset_name`` fixed regardless of the corpus id.
    """

    name: str
    source_factory: Callable[..., TaskSource]
    dataset_name: str
    default_dataset_version: str
    description: str
    corpus_id_names_dataset: bool = False


IMPORTERS: dict[str, ImporterSpec] = {
    "dir": ImporterSpec(
        name="dir",
        source_factory=lambda source, image_template=None: DirectorySource(source),
        dataset_name=TERMINAL_BENCH,
        default_dataset_version="2.0",
        description="harbor json dir",
        corpus_id_names_dataset=True,
    ),
    "swebench": ImporterSpec(
        name="swebench",
        source_factory=lambda source, image_template=None: SweBenchSource(
            source, image_template=image_template
        ),
        dataset_name=SWEBENCH,
        # do NOT inherit terminal-bench's "2.0"; the user labels the export.
        default_dataset_version="SWE-bench_Verified",
        description="SWE-bench export",
    ),
}


def importer_names() -> str:
    """The registered importer names, ``' | '``-joined — the refusal-message and
    error-list rendering of :data:`IMPORTERS`."""
    return " | ".join(IMPORTERS)


def importer_help() -> str:
    """The ``--benchmark`` flag help, derived from :data:`IMPORTERS` so a new
    importer's name + description appear without editing a second string."""
    return "Source format: " + " | ".join(
        f"'{name}' ({spec.description})" for name, spec in IMPORTERS.items()
    )
