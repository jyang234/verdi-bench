"""Fluent experiment builder — the write path's front door [refactor 02 §2].

``Experiment`` collects arms / judge / tasks / decision into a dict and validates
it through the **existing** pydantic models (``ExperimentSpec.from_dict`` and
``TaskSpec``) — one validation source, zero new rules (02 §2). It owns no
validation of its own: a rejected spec raises the same named ``SpecError`` the
CLI and lock raise. ``.write(dir)`` serializes pre-lock only (``spec_to_yaml`` /
``tasks_to_yaml``), never rewriting a locked file (07 §invariants).

Determinism/cost directives forbid silent defaults for ``seed`` and the cost
ceiling — both are required constructor arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from .workspace import ExperimentWorkspace


@dataclass(frozen=True)
class Task:
    """A ``tasks.yaml`` entry, value-object form — mirrors :class:`~harness.schema.tasks.TaskSpec`.

    Every field maps 1:1 onto ``TaskSpec`` (the write-side, ``extra="forbid"``
    model the builder validates through). ``fake_behavior`` is the fake engine's
    deterministic scripting hook (native-log injection for the hermetic paths).

    ``holdout`` is a **Phase-3 seam** ([05](05-grading-judging.md) §1): a
    first-class ``Holdout`` object that will ``materialize()`` into
    ``holdouts/<id>/`` and set ``holdouts_dir`` at ``.write(...)`` time. It is
    accepted here so the value-object shape is stable, but wiring it is not yet
    implemented — using it raises loudly rather than silently dropping a grading
    contract (fail-loudly directive). Until then, point ``holdouts_dir`` at a
    tree you materialize yourself, or use the fake path's ``write_holdout_results``.
    """

    id: str
    prompt: str = ""
    image: Optional[str] = None
    task_class: Optional[str] = None
    holdouts_dir: Optional[str] = None
    holdout_canaries: tuple[str, ...] = ()
    timeout_s: Optional[int] = None
    plugin_ids: tuple[str, ...] = ()
    fake_behavior: Optional[dict] = None
    holdout: Optional[object] = None  # Phase-3 seam; see the class docstring.

    def to_spec_dict(self) -> dict:
        """The minimal ``TaskSpec`` kwargs — unset optionals omitted so the
        emitted ``tasks.yaml`` stays the lean file the lenient reader re-reads."""
        if self.holdout is not None:
            raise NotImplementedError(
                "Task(holdout=...) is a Phase-3 seam (the Holdout hierarchy, "
                "refactor 05 §1) and is not wired yet; set holdouts_dir to a "
                "materialized tree, or use the fake path's write_holdout_results"
            )
        out: dict = {"id": self.id}
        if self.prompt:
            out["prompt"] = self.prompt
        if self.image is not None:
            out["image"] = self.image
        if self.task_class is not None:
            out["task_class"] = self.task_class
        if self.holdouts_dir is not None:
            out["holdouts_dir"] = self.holdouts_dir
        if self.holdout_canaries:
            out["holdout_canaries"] = list(self.holdout_canaries)
        if self.timeout_s is not None:
            out["timeout_s"] = self.timeout_s
        if self.plugin_ids:
            out["plugin_ids"] = list(self.plugin_ids)
        if self.fake_behavior:
            out["fake_behavior"] = self.fake_behavior
        return out


@dataclass
class _JudgeCfg:
    model: str
    rubric: Union[str, Path, None]
    orders: str
    temperature: float
    escalation: Optional[dict]


class Experiment:
    """A thin, fluent builder; collects, then validates through the real models.

    Seed and cost ceiling are required (no silent defaults for the determinism /
    cost-fence contracts). Corpus, decision rule, and repetitions have documented
    defaults so the smallest useful A/B needs only arms, a judge, and tasks — the
    north-star UX (master plan §3).
    """

    # The SDK's on-disk rubric path convention: the builder writes the resolved
    # rubric text here and points judge.rubric at it. (The `bench init` / author
    # scaffold template uses its own path; these are independent surfaces.)
    RUBRIC_FILENAME = "rubric.md"

    def __init__(
        self, name: str, *, seed: int, cost_ceiling_usd: float, currency: str = "USD"
    ) -> None:
        self.name = name
        self._seed = seed
        self._cost_ceiling = {"amount": cost_ceiling_usd, "currency": currency}
        self._arms: list[dict] = []
        self._judge: Optional[_JudgeCfg] = None
        self._tasks: list[Task] = []
        self._corpus: Optional[dict] = None
        self._repetitions: int = 1
        self._primary_metric: str = "holdout_pass_rate"
        self._decision_rule: str = "delta_holdout_pass_rate > 0"
        self._multi_arm_correction: Optional[str] = None
        self._contamination: Optional[dict] = None
        self._run_config: Optional[dict] = None

    # --- fluent collectors ----------------------------------------------------
    def arm(
        self, name: str, *, model: str, platform: str = "generic",
        image: Optional[str] = None, payload: Optional[dict] = None,
        training_cutoff: Optional[str] = None, aux_models: tuple = (),
        model_hosts: Optional[dict] = None,
    ) -> "Experiment":
        """Append an arm. ``image`` is a **Phase-3 seam** — per-arm image binding
        (``official_image(...)``) lands with the harbor/images work (refactor 03);
        setting it now raises rather than being silently dropped."""
        if image is not None:
            raise NotImplementedError(
                "Experiment.arm(image=...) is a Phase-3 seam (per-arm image "
                "binding, refactor 03); set the image on the Task for now"
            )
        arm: dict = {"name": name, "platform": platform, "model": model,
                     "payload": dict(payload or {})}
        if training_cutoff is not None:
            arm["training_cutoff"] = training_cutoff
        if aux_models:
            # accept model-id strings or {model, training_cutoff} dicts
            arm["aux_models"] = [
                {"model": a} if isinstance(a, str) else dict(a) for a in aux_models
            ]
        if model_hosts:
            arm["model_hosts"] = dict(model_hosts)
        self._arms.append(arm)
        return self

    def judge(
        self, model: str, *, rubric: Union[str, Path, None] = None,
        orders: str = "both", temperature: float = 0, escalation: Optional[dict] = None,
    ) -> "Experiment":
        """Configure the judge. ``rubric=None`` uses the library rubric template
        (the single source of the verdict-JSON contract, decision A8); a ``str``
        is literal rubric text; a ``Path`` is a rubric file to read."""
        self._judge = _JudgeCfg(model, rubric, orders, temperature, escalation)
        return self

    def task(self, task: Task) -> "Experiment":
        """Append a task (a :class:`Task` value object)."""
        self._tasks.append(task)
        return self

    def corpus(self, id: str, version: str) -> "Experiment":
        self._corpus = {"id": id, "version": version}
        return self

    def repetitions(self, n: int) -> "Experiment":
        self._repetitions = n
        return self

    def decision(
        self, metric: str = "holdout_pass_rate", op: str = ">", threshold: float = 0.0
    ) -> "Experiment":
        """Set the primary metric and its decision rule (``delta_<metric> <op>
        <threshold>``). An integer-valued threshold renders without a trailing
        ``.0`` (``> 0``, not ``> 0.0``)."""
        self._primary_metric = metric
        thr = int(threshold) if float(threshold).is_integer() else threshold
        self._decision_rule = f"delta_{metric} {op} {thr}"
        return self

    def multi_arm_correction(self, correction: str) -> "Experiment":
        """Set the family-wise correction for >2-arm designs (``none`` | ``holm``)."""
        self._multi_arm_correction = correction
        return self

    def contamination(self, *, overlap_threshold: float) -> "Experiment":
        """Pre-register the contamination overlap threshold (rides the lock)."""
        self._contamination = {"overlap_threshold": overlap_threshold}
        return self

    def run_config(self, config: dict) -> "Experiment":
        """Attach an operational ``run.config.yaml`` mapping (proxy, provider-key
        names, quotas). Written verbatim by :meth:`write`; it is NOT part of the
        sha-locked pre-registration (operational config, never the spec)."""
        self._run_config = config
        return self

    # --- terminals ------------------------------------------------------------
    def _rubric_text(self) -> str:
        assert self._judge is not None
        r = self._judge.rubric
        if r is None:
            from .templates import judge_rubric_text

            return judge_rubric_text()
        if isinstance(r, Path):
            return r.read_text(encoding="utf-8")
        return r

    def _spec_dict(self) -> dict:
        if len(self._arms) < 2:
            raise ValueError("an experiment needs >= 2 arms; call .arm(...) twice")
        if self._judge is None:
            raise ValueError("no judge configured; call .judge(model, ...)")
        if not self._tasks:
            raise ValueError("no tasks added; call .task(Task(...))")
        judge: dict = {
            "model": self._judge.model,
            "rubric": self.RUBRIC_FILENAME,
            "orders": self._judge.orders,
            "temperature": self._judge.temperature,
        }
        if self._judge.escalation is not None:
            judge["escalation"] = self._judge.escalation
        spec: dict = {
            "arms": self._arms,
            "corpus": self._corpus or {"id": self.name, "version": "1.0.0"},
            "repetitions": self._repetitions,
            "primary_metric": self._primary_metric,
            "decision_rule": self._decision_rule,
            "judge": judge,
            "seed": self._seed,
            "cost_ceiling": self._cost_ceiling,
        }
        if self._multi_arm_correction is not None:
            spec["multi_arm_correction"] = self._multi_arm_correction
        if self._contamination is not None:
            spec["contamination"] = self._contamination
        return spec

    def build(self):
        """Validate and return ``(ExperimentSpec, list[TaskSpec], rubric_text)``.

        Both models are the existing pydantic validators — the *only* source of
        spec/task rejection (a bad spec raises the named ``SpecError``; an unknown
        task key raises ``extra="forbid"``)."""
        from ..schema.experiment import ExperimentSpec
        from ..schema.tasks import TaskSpec

        spec = ExperimentSpec.from_dict(self._spec_dict())
        task_specs = [TaskSpec(**t.to_spec_dict()) for t in self._tasks]
        return spec, task_specs, self._rubric_text()

    def write(self, dir) -> "ExperimentWorkspace":
        """Serialize the experiment pre-lock and return an ``ExperimentWorkspace``.

        Writes ``experiment.yaml`` (``spec_to_yaml``), ``tasks.yaml``
        (``tasks_to_yaml``), the judge rubric at ``judge.rubric``, and
        ``run.config.yaml`` when one was attached. **Pre-lock only:** refuses if a
        ``ledger.ndjson`` already exists — a written directory is one the lock may
        already have hashed, and nothing may rewrite a locked file (07 §invariants).
        """
        import yaml

        from ..schema.serialize import spec_to_yaml
        from ..schema.tasks import tasks_to_yaml
        from .workspace import ExperimentWorkspace

        dir = Path(dir)
        if (dir / "ledger.ndjson").exists():
            raise FileExistsError(
                f"{dir} already has a ledger.ndjson — refusing to (re)write a "
                "possibly-locked experiment; the write path is pre-lock only"
            )
        spec, task_specs, rubric_text = self.build()
        dir.mkdir(parents=True, exist_ok=True)
        (dir / "experiment.yaml").write_text(spec_to_yaml(spec), encoding="utf-8")
        (dir / "tasks.yaml").write_text(tasks_to_yaml(task_specs), encoding="utf-8")
        rubric_path = dir / spec.judge.rubric
        rubric_path.parent.mkdir(parents=True, exist_ok=True)
        rubric_path.write_text(rubric_text, encoding="utf-8")
        if self._run_config is not None:
            (dir / "run.config.yaml").write_text(
                yaml.safe_dump(self._run_config, sort_keys=False), encoding="utf-8"
            )
        return ExperimentWorkspace(dir)
