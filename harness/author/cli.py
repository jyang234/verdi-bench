"""``bench author`` — host the pre-registration authoring surface [EVAL-17 AC-4].

The actor binds at launch through the same ``resolve_actor`` discipline every
ledgering verb uses — refused loudly when unresolvable, never "unknown" —
because the ceremony's lock event records who acted. Loopback by default:
this surface mutates (drafts + the lock), so exposing it is a deliberate act.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer


def register(app: typer.Typer) -> None:
    @app.command()
    def author(
        root: Path = typer.Argument(
            ..., help="Workspace root; drafts are plain directories under it"
        ),
        host: str = typer.Option(
            None, "--host", help="Bind address (default 127.0.0.1 — loopback only)"
        ),
        port: int = typer.Option(
            None, "--port", help="Port (default 8390; 0 = OS-assigned)"
        ),
        actor: Optional[str] = typer.Option(
            None, "--actor", help="Actor recorded on the lock event [GR-12]"
        ),
    ) -> None:
        """Draft, validate, preview, and lock experiments (one ledgered op: the lock)."""
        from ..ledger.actor import ActorResolutionError, resolve_actor
        from .server import DEFAULT_AUTHOR_PORT, DEFAULT_HOST, make_author_server

        try:
            resolved = resolve_actor(actor)
        except ActorResolutionError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        srv = make_author_server(
            Path(root),
            actor=resolved,
            host=host if host is not None else DEFAULT_HOST,
            port=port if port is not None else DEFAULT_AUTHOR_PORT,
        )
        bound_host, bound_port = srv.server_address[:2]
        typer.echo(
            f"authoring {root} at http://{bound_host}:{bound_port}/ "
            f"(actor {resolved}; previews are reads, the lock is the one ledgered "
            "operation; Ctrl-C to stop)"
        )
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            typer.echo("authoring surface stopped")
        finally:
            srv.server_close()
