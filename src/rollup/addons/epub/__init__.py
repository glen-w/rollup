"""Rich EPUB digest output writer (requires ``rollup[epub]`` / ebooklib)."""

from __future__ import annotations

import argparse
from pathlib import Path

from rollup.addons.epub.render import (
    EPUB_DEPENDENCY_HINT,
    atomic_write_epub_digest,
    ebooklib_available,
    render_epub_bytes,
)
from rollup.config import Config
from rollup.models import DigestReport
from rollup.output_writers import (
    OutputWriterError,
    WriteContext,
    requested_writer_names,
)


class EpubOutputWriter:
    """Write a rich EPUB digest with TOC, chapters, and offline summaries (no links)."""

    name = "epub"

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        del config
        requested = requested_writer_names(args)
        return requested is None or "epub" in requested

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        if ctx.dry_run:
            return []
        if not ebooklib_available():
            if not ctx.explicit_outputs:
                log = ctx.logger
                if log is not None:
                    log.warning("%s — skipping epub", EPUB_DEPENDENCY_HINT)
                return []
            raise OutputWriterError(EPUB_DEPENDENCY_HINT)
        data = render_epub_bytes(
            report,
            ctx.max_display_links,
            run_id_short=ctx.run_id_short,
        )
        path = atomic_write_epub_digest(
            ctx.output_dir,
            ctx.generated_at,
            data,
            run_id_short=ctx.run_id_short,
        )
        return [path]


__all__ = [
    "EPUB_DEPENDENCY_HINT",
    "EpubOutputWriter",
    "atomic_write_epub_digest",
    "ebooklib_available",
    "render_epub_bytes",
]
