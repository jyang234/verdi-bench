"""``bench images build/verify/list`` — trial-image operator verbs [refactor 03 §4].

Thin shells over :mod:`harness.images.build`, :mod:`harness.images.verify`, and
:mod:`harness.images.registry`. These ledger **nothing** (no entrypoint
registration): building or verifying an image is operational infrastructure, not
an auditable experiment event — the same stance as ``bench proxy``. Registered
from ``harness/cli.py``'s stage list.
"""

from __future__ import annotations

from typing import Optional

import typer


def register(app: typer.Typer) -> None:
    images_app = typer.Typer(
        help="Trial-image build / pin / verify / list [refactor 03 §4].",
        no_args_is_help=True,
    )
    app.add_typer(images_app, name="images")

    @images_app.command("build")
    def images_build(
        target: str = typer.Argument(
            ..., help="official image name (see `bench images list`) or a build-context path"
        ),
        pin: bool = typer.Option(
            False, "--pin", help="also print the runnable sha256-pinned ref"
        ),
    ) -> None:
        """Build an image (satisfying FROM verdi-base first) and pin it to a digest."""
        from .build import ImageBuildError, ImageResolveError, build
        from .registry import UnknownImageError, resolve

        try:
            spec = resolve(target)
            pinned = build(spec)
        except (UnknownImageError, ImageBuildError, ImageResolveError) as e:
            typer.echo(f"build failed: {e}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"built {spec.ref}")
        typer.echo(f"  digest: {pinned.digest}")
        if pin:
            typer.echo(f"  pinned ref: {pinned.pinned_ref}")

    @images_app.command("verify")
    def images_verify(
        ref: str = typer.Argument(..., help="image ref to verify (tag or digest-pinned)"),
        fmt: str = typer.Option(
            "generic", "--format", help="declared log format: generic | native"
        ),
        platform: Optional[str] = typer.Option(
            None, "--platform", help="adapter platform when --format native (claude_code | codex)"
        ),
    ) -> None:
        """Offline compliance check: run the image hardened + network-none and
        assert the harbor contract holds (nonzero exit = NON-COMPLIANT)."""
        from .verify import verify

        report = verify(ref, expected_format=fmt, platform=platform)
        for check in report.checks:
            mark = "PASS" if check.ok else "FAIL"
            typer.echo(f"  [{mark}] {check.name}: {check.detail}")
        if report.ok:
            typer.echo(f"COMPLIANT: {ref}")
            return
        failure = report.first_failure
        typer.echo(
            f"NON-COMPLIANT: {ref} — {failure.name}: {failure.detail}", err=True
        )
        raise typer.Exit(code=1)

    @images_app.command("list")
    def images_list() -> None:
        """List the official images: name, tag, and what each is."""
        from .registry import official_images

        for img in official_images():
            typer.echo(f"{img.name}\t{img.tag}\t{img.description}")
