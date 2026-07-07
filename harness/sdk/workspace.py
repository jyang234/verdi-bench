"""``ExperimentWorkspace`` — the stage facade over one experiment dir [refactor 02 §5].

Every method is a one-line delegation to a Phase-1 stage API (``run/api.py``,
``grade/api.py``, …); the facade owns *no* logic (single-responsibility
directive). It gives a library consumer — and the hermetic shakedown scripts — a
third option beyond ``CliRunner``/subprocess where the CLI itself is not under
test: drive the verbs in-process, read the ledger through :class:`LedgerView`.

**Fail loudly (the one place the facade adds behavior).** Three stage APIs return
certain refusals as *outcome flags* rather than exceptions, because the CLI shell
echoes them in a specific order: ``run``'s ``quarantine_error``,
``contamination_probe``'s ``probe_error``, and ``corpus admit``'s
``persist_error``. A CLI maps those to stderr + an exit code; a *library* caller
that ignored the returned flag would silently proceed past a refusal. So the
facade re-raises them as typed exceptions — a refusal a consumer cannot miss.
CLI behavior is untouched (the CLIs keep consuming ``api.py`` directly).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from ..errors import VerdiRefusal
from ..ledger.view import LedgerView


class WorkspaceError(VerdiRefusal, RuntimeError):
    """Base for facade-raised refusals that the stage APIs return as flags."""


class RunQuarantineRefusal(WorkspaceError):
    """``run`` refused to schedule because a task version is quarantined."""


class ContaminationProbeRefusal(WorkspaceError):
    """``contamination probe`` refused before ledgering (broken holdout/probe)."""


class CorpusAdmitPersistError(WorkspaceError):
    """``corpus admit`` ledgered the admission but failed to persist the manifest
    /embedded copy — the admission is on the chain and must be reconciled."""


def write_holdout_results(workspace_dir, passed: bool, *, assertion_id: str = "h1") -> dict:
    """Fake-path operator step: write one trial workspace's ``holdout_results.json``.

    The arm-blind fake engine reads only ``task.fake_behavior``, never the arm, so
    a decisive A/B (or a single-arm gaming signal) is produced by the operator
    writing per-arm grader output between ``run`` and ``grade`` — exactly how the
    shipped e2e tests do it (``docs/design/shakedown.md`` honest caveats). This is
    that step, named and public in the SDK, so the shakedown scripts no longer
    reach into ``tests.fixtures`` for it. Returns the payload written.
    """
    payload = {"assertions": [{"id": assertion_id,
                               "result": "pass" if passed else "fail"}]}
    (Path(workspace_dir) / "holdout_results.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload


class ExperimentWorkspace:
    """A facade over ``<exp_dir>/{experiment.yaml, tasks.yaml, ledger.ndjson}``."""

    def __init__(self, exp_dir: Path | str) -> None:
        self.dir = Path(exp_dir)

    @property
    def ledger(self) -> Path:
        """The ``ledger.ndjson`` beside the spec (the hash-chained event log)."""
        return self.dir / "ledger.ndjson"

    def _ctx(self, actor: Optional[str]):
        """Build the ``EventContext`` the ledgering stage APIs need without the
        CLI's ``typer.Exit`` mapping — an unresolvable actor raises loudly."""
        from ..ledger.actor import resolve_actor
        from ..ledger.events import EventContext
        from ..ledger.identity import derive_experiment_id

        # [ux-friction AC-1] one shared seam: resolve self.dir before naming, so a
        # Workspace(".") stamps the experiment's real name rather than ''.
        return EventContext(
            experiment_id=derive_experiment_id(self.dir), actor=resolve_actor(actor)
        )

    # --- pre-registration + execution ----------------------------------------
    def plan(self, *, actor: Optional[str] = None, acknowledge_underpowered: bool = False,
             attested_by: Optional[str] = None, corpus_manifest: Optional[Path] = None):
        """Validate, power-check, and write the genesis lock event (``LockOutcome``)."""
        from ..plan.api import plan_experiment

        return plan_experiment(
            self.dir / "experiment.yaml", self.ledger,
            acknowledge_underpowered=acknowledge_underpowered,
            attested_by=attested_by, corpus_manifest=corpus_manifest, actor=actor,
        )

    def run(self, *, engine: str = "fake", actor: Optional[str] = None,
            corpus_manifest: Optional[Path] = None, reuse_control: Optional[Path] = None):
        """Execute the locked interleaved trials (``RunOutcome``). Raises
        :class:`RunQuarantineRefusal` on a schedule-time quarantine refusal."""
        from ..run.api import run_experiment

        outcome = run_experiment(
            self.dir, engine=engine, actor=actor,
            corpus_manifest=corpus_manifest, reuse_control=reuse_control,
        )
        if outcome.quarantine_error is not None:
            raise RunQuarantineRefusal(outcome.quarantine_error)
        return outcome

    def grade(self, *, runner: str = "docker", retry_terminal: Optional[list] = None,
              actor: Optional[str] = None):
        """Grade every ungraded trial deterministically (``GradeOutcome``)."""
        from ..grade.api import grade_experiment

        return grade_experiment(
            self.dir, runner=runner, retry_terminal=retry_terminal, actor=actor
        )

    def judge(self, *, actor: Optional[str] = None):
        """Judge every graded comparison; one verdict each (``JudgeOutcome``)."""
        from ..judge.api import judge_experiment

        return judge_experiment(self.dir, actor=actor)

    # --- corpus / calibration -------------------------------------------------
    def calibrate(self, *, manifest_path: Path, kind: str = "full",
                  rho: float = 0.3, actor: Optional[str] = None):
        """Record a calibration run from the realized variance (``CalibrateOutcome``)."""
        from ..corpus.api import corpus_calibrate

        return corpus_calibrate(
            self.dir, manifest_path=manifest_path, kind=kind, rho=rho, actor=actor
        )

    def corpus_admit(self, *, manifest_path: Path, candidate_id: str, task_sha: str,
                     baseline_ref: str, keyring: Path, candidate_json: Optional[Path] = None,
                     actor: Optional[str] = None):
        """Admit a curated candidate (``AdmitOutcome``). Raises
        :class:`CorpusAdmitPersistError` if the admission was ledgered but the
        manifest/embedded copy could not be persisted [PRA-M11]."""
        from ..corpus.api import corpus_admit

        outcome = corpus_admit(
            self.dir, manifest_path=manifest_path, candidate_id=candidate_id,
            task_sha=task_sha, baseline_ref=baseline_ref, keyring=keyring,
            candidate_json=candidate_json, actor=actor,
        )
        if outcome.persist_error is not None:
            raise CorpusAdmitPersistError(outcome.persist_error)
        return outcome

    # --- analysis surfaces ----------------------------------------------------
    def selfcheck(self, *, actor: str = "unknown", n_sim: int = 200,
                  n_boot: int = 10_000) -> dict:
        """Compute + ledger the D008 coverage selfcheck; return the result."""
        from ..analyze.api import run_selfcheck_cli

        return run_selfcheck_cli(self.dir, actor=actor, n_sim=n_sim, n_boot=n_boot)

    def analyze(self, *, exploratory: bool = False, official_corpus: Optional[Path] = None,
                html: bool = False, actor: str = "unknown") -> Optional[Path]:
        """Render findings behind the fence; return the render path, or ``None`` on
        a fail-closed refusal (which ledgers exactly one ``cant_analyze`` — a
        first-class, observable disposition, so it is returned, not raised)."""
        from ..analyze.api import run_analyze

        mode = "exploratory" if exploratory else "official"
        return run_analyze(
            self.dir, mode=mode, corpus=official_corpus, html=html, actor=actor
        )

    def card(self, *, corpus: Optional[Path] = None, fmt: str = "json",
             out: Optional[Path] = None):
        """Build + render the benchmark result card (``CardEmitOutcome``)."""
        from ..analyze.api import emit_card

        return emit_card(self.dir, corpus=corpus, fmt=fmt, out=out)

    # --- forensics ------------------------------------------------------------
    def forensics(self, *, actor: Optional[str] = None, review: bool = True,
                  model: Optional[str] = None):
        """Scan every trial into exactly one ``forensics_report`` (``ForensicsScanOutcome``)."""
        from ..forensics.api import forensics_scan

        return forensics_scan(self.dir, ctx=self._ctx(actor), review=review, model=model)

    def forensics_record(self, *, trial_id: str, labels: dict,
                         stratum: str = "mandatory", actor: Optional[str] = None) -> None:
        """Record a human per-detector spot-check [AC-4, D006]."""
        from ..forensics.api import forensics_record

        forensics_record(
            self.dir, ctx=self._ctx(actor), trial_id=trial_id, labels=labels,
            stratum=stratum,
        )

    def quarantine(self, *, trial_id: str, reason: str, actor: Optional[str] = None) -> None:
        """Ledger the operator disposition excluding a trial, disclosed [D007]."""
        from ..forensics.api import quarantine

        quarantine(self.dir, ctx=self._ctx(actor), trial_id=trial_id, reason=reason)

    # --- review (offline blinded human pass) ----------------------------------
    def review_build(self, *, out: Optional[Path] = None, actor: Optional[str] = None):
        """Sample + render the blinded review packet (``ReviewBuildOutcome``)."""
        from ..review.api import review_build

        return review_build(self.dir, out=out, actor=actor)

    def review_record(self, *, comparison_id: str, winner: str, reason: str = "",
                      arm_recognized: bool = False, arm_guess=None,
                      actor: Optional[str] = None) -> None:
        """Record a human verdict + the two integrity answers (strictly pre-reveal)."""
        from ..review.api import review_record

        review_record(
            self.dir, comparison_id=comparison_id, winner=winner, reason=reason,
            arm_recognized=arm_recognized, arm_guess=arm_guess, actor=actor,
        )

    def review_reveal(self, *, comparison_id: str, actor: Optional[str] = None) -> dict:
        """Unblind a comparison — refuses before a verdict exists. Returns the map."""
        from ..review.api import review_reveal

        return review_reveal(self.dir, comparison_id=comparison_id, actor=actor)

    # --- process scoring ------------------------------------------------------
    def process_score(self, *, rubric_path: Optional[Path] = None,
                     actor: Optional[str] = None):
        """Judge-score every unscored trial's process (``ProcessScoreOutcome``)."""
        from ..process.api import process_score

        return process_score(self.dir, rubric_path=rubric_path, actor=actor)

    def process_record(self, *, trial_id: str, comparison_id: str, scores: dict,
                      rubric, actor: Optional[str] = None) -> None:
        """Record a human process score — refused before the EVAL-7 reveal."""
        from ..process.api import process_record

        process_record(
            self.dir, trial_id=trial_id, comparison_id=comparison_id,
            scores=scores, rubric=rubric, actor=actor,
        )

    # --- contamination --------------------------------------------------------
    def contamination_probe(self, *, manifest_path: Optional[Path] = None,
                           oracle_dir: Optional[Path] = None, scan_artifacts: bool = True,
                           actor: Optional[str] = None):
        """Probe every arm model for training-set membership (``ContaminationProbeOutcome``).
        Raises :class:`ContaminationProbeRefusal` on a probe-time refusal."""
        from ..contamination.api import contamination_probe

        outcome = contamination_probe(
            self.dir, ctx=self._ctx(actor), manifest_path=manifest_path,
            oracle_dir=oracle_dir, scan_artifacts=scan_artifacts,
        )
        if outcome.probe_error is not None:
            raise ContaminationProbeRefusal(outcome.probe_error)
        return outcome

    # --- ledger integrity + reads ---------------------------------------------
    def verify_chain(self, *, anchors: Optional[Path] = None):
        """Verify the hash chain (and optionally an external anchor) (``ChainVerdict``)."""
        from ..ledger.api import verify_chain

        return verify_chain(self.ledger, against_anchor=anchors)

    def anchor(self, *, out: Path, actor: Optional[str] = None):
        """Record the chain head to an external anchor store (``AnchorOutcome``)."""
        from ..ledger.api import anchor

        return anchor(self.ledger, out=out, actor=actor)

    def status(self) -> dict:
        """The read-only lifecycle snapshot (the ledger + heartbeat projection)."""
        from ..status.aggregate import compute_status

        return compute_status(self.dir)

    def view(self, *, verify: bool = False) -> LedgerView:
        """A :class:`LedgerView` over the ledger — typed, memoized reads."""
        return LedgerView(self.ledger, verify=verify)

    # --- fake-path operator step ---------------------------------------------
    def inject_holdout_results(
        self, passes: Callable[[str, str], bool], *, assertion_id: str = "h1"
    ) -> int:
        """Write per-trial ``holdout_results.json`` for every ledgered trial, the
        result decided by ``passes(arm, task_id)`` — the operator step the
        arm-blind fake engine needs for a decisive A/B (see
        :func:`write_holdout_results`). Returns the number of workspaces written.
        """
        n = 0
        for tv in self.view().trials():
            rec = tv.record
            ws = Path(rec["artifacts_path"]).parent
            ws.mkdir(parents=True, exist_ok=True)
            write_holdout_results(
                ws, passes(rec["arm"], rec["task_id"]), assertion_id=assertion_id
            )
            n += 1
        return n
