"""OPDS catalog writer for a finished briefing (not a read-later library)."""

from __future__ import annotations

import argparse
from pathlib import Path

from rollup.addons.opds.render import atomic_write_opds_digest, render_opds_atom
from rollup.config import Config
from rollup.models import DigestReport
from rollup.output_writers import WriteContext, requested_writer_names


class OpdsOutputWriter:
    """Write an OPDS 1.2 acquisition feed for this run's briefing artifacts."""

    name = "opds"
    required = False

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        del config
        requested = requested_writer_names(args)
        return requested is None or "opds" in requested

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        if ctx.dry_run:
            return []
        xml = render_opds_atom(
            report,
            ctx.output_dir,
            run_id_short=ctx.run_id_short,
        )
        path = atomic_write_opds_digest(
            ctx.output_dir,
            ctx.generated_at,
            xml,
            run_id_short=ctx.run_id_short,
        )
        return [path]


__all__ = [
    "OpdsOutputWriter",
    "atomic_write_opds_digest",
    "render_opds_atom",
]
