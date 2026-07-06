"""``bench proxy up/down`` — operator verbs for the managed metering proxy [refactor 04 §1].

Thin shells over :class:`~harness.hermetic.metering.MeteringProxy`. These ledger
**nothing** (no entrypoint registration): standing a proxy up is operational
infrastructure, not an auditable experiment event. Registered from
``harness/cli.py``'s stage list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from harness.hermetic.metering import (
    MANAGED_PROXY_NAME,
    MeteringProxy,
    MeteringProxyError,
    teardown_managed,
)


def register(app: typer.Typer) -> None:
    proxy_app = typer.Typer(
        help="Managed metering proxy lifecycle [refactor 04 §1].", no_args_is_help=True
    )
    app.add_typer(proxy_app, name="proxy")

    @proxy_app.command("up")
    def proxy_up(
        allow: list[str] = typer.Option(
            ..., "--allow", help="An allowlisted host the proxy may tunnel to (repeatable)"
        ),
        log_path: Path = typer.Option(
            Path("verdi-metering.jsonl"),
            "--log-path",
            help="Where the proxy's per-trial JSONL log lands (host path)",
        ),
    ) -> None:
        """Stand up the metered + egress networks and the CONNECT proxy, then leave
        it running (tear it down with ``bench proxy down``)."""
        try:
            cfg = MeteringProxy(list(allow), log_path=log_path).start()
        except MeteringProxyError as e:
            typer.echo(f"metering proxy did not come up: {e}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"metering proxy up: {cfg.proxy_url}")
        typer.echo(f"  allowlist: {', '.join(cfg.allowlist)}")
        typer.echo(f"  log: {cfg.log_path}")

    @proxy_app.command("down")
    def proxy_down(
        name: str = typer.Option(
            MANAGED_PROXY_NAME, "--name", help="Proxy container name to remove"
        ),
    ) -> None:
        """Remove the managed proxy container and its metered + egress networks."""
        teardown_managed(name=name)
        typer.echo(f"metering proxy {name!r} and its networks removed")
