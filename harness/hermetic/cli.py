"""``bench proxy up/down`` + ``bench otlp up/down`` — operator verbs for the
managed metering proxy [refactor 04 §1] and trace collector [refactor 09 §3].

Thin shells over :class:`~harness.hermetic.metering.MeteringProxy` and
:class:`~harness.hermetic.tracing.TraceCollector`. These ledger **nothing** (no
entrypoint registration): standing a sidecar up is operational infrastructure,
not an auditable experiment event. Registered from ``harness/cli.py``'s stage list.
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
from harness.hermetic.tracing import (
    MANAGED_COLLECTOR_NAME,
    TraceCollector,
    TraceCollectorError,
)
from harness.hermetic.tracing import teardown_managed as teardown_collector


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
        proxy = MeteringProxy(list(allow), log_path=log_path)
        try:
            cfg = proxy.start()
        except MeteringProxyError as e:
            # A partial stand-up (networks made, container crashed) must not
            # leak until `bench proxy down` [P3 interim review M4].
            proxy.stop()
            typer.echo(f"metering proxy did not come up: {e}", err=True)
            raise typer.Exit(code=1)
        except BaseException:
            proxy.stop()
            raise
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

    otlp_app = typer.Typer(
        help="Managed OTLP trace-collector lifecycle [refactor 09 §3].", no_args_is_help=True
    )
    app.add_typer(otlp_app, name="otlp")

    @otlp_app.command("up")
    def otlp_up(
        log_path: Path = typer.Option(
            Path("verdi-otlp.jsonl"),
            "--log-path",
            help="Where the collector's envelope JSONL lands (host path)",
        ),
        keep_raw: bool = typer.Option(
            False,
            "--keep-raw",
            help="Retain the raw envelope log after teardown (D-09-1 opt-in); "
            "by default `bench otlp down` deletes it",
        ),
    ) -> None:
        """Stand up the metered network and the OTLP trace collector, then leave it
        running (tear it down with ``bench otlp down``)."""
        collector = TraceCollector(log_path=log_path, keep_raw=keep_raw)
        try:
            cfg = collector.start()
        except TraceCollectorError as e:
            # A partial stand-up (network made, container crashed) must not leak
            # until `bench otlp down` [P3 interim review M4].
            collector.stop()
            typer.echo(f"trace collector did not come up: {e}", err=True)
            raise typer.Exit(code=1)
        except BaseException:
            collector.stop()
            raise
        typer.echo(f"trace collector up: {cfg.endpoint}")
        typer.echo(f"  envelope log: {cfg.log_path}")
        typer.echo(
            "  raw log: retained (operator-tier)"
            if keep_raw
            else "  raw log: deleted by `bench otlp down` unless it is passed --keep-raw [D-09-1]"
        )

    @otlp_app.command("down")
    def otlp_down(
        name: str = typer.Option(
            MANAGED_COLLECTOR_NAME, "--name", help="Collector container name to remove"
        ),
        log_path: Path = typer.Option(
            Path("verdi-otlp.jsonl"),
            "--log-path",
            help="The envelope log to delete on teardown (D-09-1)",
        ),
        keep_raw: bool = typer.Option(
            False, "--keep-raw", help="Retain the raw envelope log (D-09-1 opt-in)"
        ),
    ) -> None:
        """Remove the managed collector container + its metered network, and delete
        the raw envelope log unless ``--keep-raw`` (the D-09-1 default) [refactor 09 §6]."""
        teardown_collector(name=name, log_path=log_path, keep_raw=keep_raw)
        kept = "retained" if keep_raw else "removed"
        typer.echo(f"trace collector {name!r} and its network removed; raw log {kept}")
