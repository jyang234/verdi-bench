"""``bench serve`` — host the read-only live observer [EVAL-13 AC-5; EVAL-14 AC-1].

Serves either one experiment directory or, with ``--root``, a workspace of
them (one-level scan for ``ledger.ndjson`` directories — D003). Loopback by
default; binding a non-loopback host exposes unblinded experiment content
(arm identities, task ids, spend) to that network — the flag exists, the
default does not.

``--bundle <out>`` [EVAL-19 AC-1, D001] writes the same view as a static,
self-contained snapshot instead of hosting it: one experiment directory in,
one deterministic HTML file out, no server started and no event appended.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer


def register(app: typer.Typer) -> None:
    @app.command()
    def serve(
        experiment_dir: Optional[Path] = typer.Argument(
            None, help="Directory with experiment.yaml + ledger.ndjson"
        ),
        root: Optional[Path] = typer.Option(
            None, "--root", help="Workspace root: serve every experiment under it"
        ),
        host: str = typer.Option(
            None, "--host", help="Bind address (default 127.0.0.1 — loopback only)"
        ),
        port: int = typer.Option(
            None, "--port", help="Port (default 8383; 0 = OS-assigned)"
        ),
        corpus_manifest: Optional[Path] = typer.Option(
            None,
            "--corpus-manifest",
            help="Manifest for the official-fence corpus items (single-experiment mode)",
        ),
        bundle: Optional[Path] = typer.Option(
            None,
            "--bundle",
            help="Write a static self-contained snapshot to this path instead of serving",
        ),
    ) -> None:
        """Live operator view (read-only, unblinded — see the page banner)."""
        from .server import DEFAULT_HOST, DEFAULT_PORT, make_server

        if (experiment_dir is None) == (root is None):
            typer.echo(
                "pass exactly one target: an <experiment-dir> or --root <workspace-dir>",
                err=True,
            )
            raise typer.Exit(code=2)
        manifest = None
        if corpus_manifest is not None:
            from ..corpus.registry import CorpusManifest

            manifest = CorpusManifest.load(corpus_manifest)
        if bundle is not None:
            if experiment_dir is None:
                typer.echo(
                    "--bundle archives one experiment: pass an <experiment-dir>, not --root",
                    err=True,
                )
                raise typer.Exit(code=2)
            from .bundle import write_bundle

            out = write_bundle(Path(experiment_dir), bundle, corpus_manifest=manifest)
            typer.echo(
                f"bundled {experiment_dir} -> {out} "
                "(static snapshot; unblinded operator view; no server started)"
            )
            return
        srv = make_server(
            Path(experiment_dir) if experiment_dir is not None else None,
            root=Path(root) if root is not None else None,
            host=host if host is not None else DEFAULT_HOST,
            port=port if port is not None else DEFAULT_PORT,
            corpus_manifest=manifest,
        )
        bound_host, bound_port = srv.server_address[:2]
        target = experiment_dir if experiment_dir is not None else f"workspace {root}"
        typer.echo(
            f"observing {target} at http://{bound_host}:{bound_port}/ "
            "(read-only; unblinded operator view; Ctrl-C to stop)"
        )
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            typer.echo("observer stopped")
        finally:
            srv.server_close()
