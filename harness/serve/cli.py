"""``bench serve`` — host the read-only live observer [EVAL-13 AC-5].

Loopback by default; binding a non-loopback host exposes unblinded experiment
content (arm identities, task ids, spend) to that network — the flag exists,
the default does not.
"""

from __future__ import annotations

from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    @app.command()
    def serve(
        experiment_dir: Path = typer.Argument(
            ..., help="Directory with experiment.yaml + ledger.ndjson"
        ),
        host: str = typer.Option(
            None, "--host", help="Bind address (default 127.0.0.1 — loopback only)"
        ),
        port: int = typer.Option(
            None, "--port", help="Port (default 8383; 0 = OS-assigned)"
        ),
    ) -> None:
        """Live operator view (read-only, unblinded — see the page banner)."""
        from .server import DEFAULT_HOST, DEFAULT_PORT, make_server

        srv = make_server(
            Path(experiment_dir),
            host=host if host is not None else DEFAULT_HOST,
            port=port if port is not None else DEFAULT_PORT,
        )
        bound_host, bound_port = srv.server_address[:2]
        typer.echo(
            f"observing {experiment_dir} at http://{bound_host}:{bound_port}/ "
            "(read-only; unblinded operator view; Ctrl-C to stop)"
        )
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            typer.echo("observer stopped")
        finally:
            srv.server_close()
