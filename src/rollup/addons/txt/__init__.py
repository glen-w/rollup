"""Plain-text (link-free) digest output writer."""

from __future__ import annotations

import argparse
from pathlib import Path

from rollup.addons.txt.render import atomic_write_txt_digest, render_txt
from rollup.config import Config
from rollup.models import DigestReport
from rollup.output_writers import WriteContext, requested_writer_names


class TxtOutputWriter:
    """Write an XTEINK-like plain-text digest with no links."""

    name = "txt"

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        del config
        requested = requested_writer_names(args)
        return requested is None or "txt" in requested

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        if ctx.dry_run:
            return []
        text = render_txt(report, ctx.max_display_links)
        path = atomic_write_txt_digest(
            ctx.output_dir,
            ctx.generated_at,
            text,
            run_id_short=ctx.run_id_short,
        )
        return [path]


__all__ = ["TxtOutputWriter", "atomic_write_txt_digest", "render_txt"]
