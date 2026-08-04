"""XTEINK e-ink optimized digest output writer."""

from __future__ import annotations

import argparse
from pathlib import Path

from rollup.addons.xteink.render import (
    atomic_write_xteink_digest,
    render_xteink_markdown,
)
from rollup.config import Config
from rollup.models import DigestReport
from rollup.output_writers import WriteContext, requested_writer_names


class XteinkOutputWriter:
    """Write XTEINK-optimized Markdown alongside the normal digest."""

    name = "xteink"

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        # Compatibility ``--xteink`` / ``--x3`` are registered by core CLI.
        del parser

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        del config
        requested = requested_writer_names(args)
        return requested is None or "xteink" in requested

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        if ctx.dry_run:
            return []
        md = render_xteink_markdown(
            report, ctx.max_display_links, ctx.folder_themes
        )
        md_path = atomic_write_xteink_digest(
            ctx.output_dir,
            ctx.generated_at,
            md,
            run_id_short=ctx.run_id_short,
        )
        return [md_path]


__all__ = [
    "XteinkOutputWriter",
    "atomic_write_xteink_digest",
    "render_xteink_markdown",
]
