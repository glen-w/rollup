"""Structured JSON digest output writer."""

from __future__ import annotations

import argparse
from pathlib import Path

from rollup.addons.json.serialize import atomic_write_json_digest, render_json
from rollup.config import Config
from rollup.models import DigestReport
from rollup.output_writers import WriteContext, requested_writer_names


class JsonOutputWriter:
    """Write a structured DigestReport JSON artifact (no raw bodies)."""

    name = "json"

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        del config
        requested = requested_writer_names(args)
        return requested is None or "json" in requested

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        if ctx.dry_run:
            return []
        text = render_json(report, ctx.max_display_links)
        path = atomic_write_json_digest(
            ctx.output_dir,
            ctx.generated_at,
            text,
            run_id_short=ctx.run_id_short,
        )
        return [path]


__all__ = ["JsonOutputWriter", "atomic_write_json_digest", "render_json"]
