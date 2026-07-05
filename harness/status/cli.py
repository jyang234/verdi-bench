"""``bench status`` — read-only lifecycle snapshot [EVAL-13 AC-4].

Ledgers nothing: the verb renders :func:`compute_status` and exits 0 even when
it is *describing* a broken state (a broken chain is reported in the payload;
``bench verify-chain`` remains the exit-code-bearing audit verb).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer


def render_text(snap: dict) -> str:
    """Human summary of one snapshot — same data as ``--json``, phrased."""
    lines = [f"experiment {snap['experiment_id']}"]
    chain = snap["chain"]
    if not chain["ok"]:
        lines.append(f"  chain    BROKEN: {chain['detail']}")
        lines.append("  stages   withheld (unverified ledger content) [fail closed]")
    else:
        head = chain["head_hash"][:12] + "…" if chain["head_hash"] else "(empty)"
        lines.append(f"  chain    OK  events={chain['events']}  head={head}")
        st = snap["stages"]
        lock = st["lock"]
        lines.append(
            f"  lock     locked  seed={lock['seed']}  sha={lock['spec_sha256'][:12]}…"
            if lock["locked"]
            else "  lock     not locked"
        )
        if st["spec_error"]:
            lines.append(f"  spec     unreadable: {st['spec_error']}")
        cells, spend = st["cells"], st["spend"]
        planned = cells["planned"] if cells["planned"] is not None else "?"
        lines.append(
            f"  run      {cells['done']}/{planned} cells"
            f"  infra_failures={cells['infra_failures']}"
        )
        for arm, c in st["per_arm"].items():
            lines.append(
                f"           arm {arm}: {c['trials']} trials"
                f" (completed={c['completed']} timeout={c['timeout']}"
                f" infra_failed={c['infra_failed']})"
            )
        ceiling = spend["ceiling"] if spend["ceiling"] is not None else "?"
        stopped = "  STOPPED (ceiling)" if spend["stopped_cost_ceiling"] else ""
        lines.append(f"  spend    {spend['accumulated']}/{ceiling}{stopped}")
        g, j = st["grade"], st["judge"]
        lines.append(
            f"  grade    graded={g['graded']}"
            f"  cant_grade_terminal={g['cant_grade_terminal']}  pending={g['pending']}"
        )
        lines.append(f"  judge    verdicts={j['verdicts']}  cant_judge={j['cant_judge']}")
        r = st["review"]
        lines.append(
            f"  review   packets={r['packets']}"
            f"  human_verdicts={r['human_verdicts']}  reveals={r['reveals']}"
        )
        f = st["forensics"]
        flags = f["latest"]["flags"] if f["latest"] else "-"
        lines.append(f"  forensics reports={f['reports']}  latest_flags={flags}")
        if st["quarantines"]:
            ids = ", ".join(q["trial_id"] for q in st["quarantines"])
            lines.append(f"  quarantined {ids}")
        a = st["analyze"]
        lines.append(
            f"  analyze  selfcheck={a['selfcheck']}"
            f"  renders(official={a['renders']['official']},"
            f" exploratory={a['renders']['exploratory']})"
        )
    hb = snap["heartbeat"]
    if hb is not None:
        flight = hb.get("in_flight")
        where = (
            f"  in_flight={flight['task_id']}/{flight['arm']}"
            f"/rep{flight['repetition']} attempt={flight['attempt']}"
            if flight
            else ""
        )
        lines.append(f"  heartbeat {hb.get('state')} ts={hb.get('ts')}{where}")
    else:
        lines.append("  heartbeat none")
    return "\n".join(lines)


def register(app: typer.Typer) -> None:
    @app.command()
    def status(
        experiment_dir: Path = typer.Argument(
            ..., help="Directory with experiment.yaml + ledger.ndjson"
        ),
        as_json: bool = typer.Option(
            False, "--json", help="Emit the snapshot as one JSON document"
        ),
    ) -> None:
        """Lifecycle snapshot from the ledger + heartbeat (read-only) [EVAL-13]."""
        from .aggregate import compute_status

        # F-M-T2: a nonexistent directory (a typo) must refuse — rendering it as
        # a healthy "chain OK (empty) / not yet planned" experiment is a silently
        # wrong answer from the observability verb. An EXISTING directory with no
        # ledger legitimately renders the empty state (that IS "not yet planned").
        if not Path(experiment_dir).is_dir():
            typer.echo(f"no such experiment directory: {experiment_dir}", err=True)
            raise typer.Exit(code=2)
        snap = compute_status(Path(experiment_dir))
        if as_json:
            typer.echo(json.dumps(snap, sort_keys=True))
        else:
            typer.echo(render_text(snap))
