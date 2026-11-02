"""Calibre-style drop-folder writer (copy briefing artifacts, no network)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from rollup.models import DigestReport
from rollup.output_writers import (
    OutputWriterError,
    WriteContext,
    requested_writer_names,
)
from rollup.render import digest_output_stem
from rollup.safety import SafetyError, assert_safe_write_paths

if TYPE_CHECKING:
    from rollup.config import Config

_COPY_SUFFIXES = (
    ".epub",
    ".atom.xml",
    ".html",
    ".md",
    ".json",
    ".txt",
    ".xteink.md",
)


class CalibreOutputWriter:
    """Copy this run's briefing files into ``[calibre].drop_dir``."""

    name = "calibre"
    required = False

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--calibre-drop-dir",
            default=None,
            metavar="DIR",
            help=(
                "Copy finished briefing artifacts into this directory "
                "(Calibre auto-add / watch folder). No network."
            ),
        )

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        requested = requested_writer_names(args)
        drop = _resolve_drop_dir(args, config)
        if requested is None:
            return drop is not None
        return "calibre" in requested

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        del report
        if ctx.dry_run:
            return []
        drop = None
        if ctx.config is not None:
            drop = ctx.config.calibre.drop_dir
        if drop is None:
            if ctx.explicit_outputs:
                raise OutputWriterError(
                    "calibre writer enabled but no drop directory; "
                    "set [calibre].drop_dir or pass --calibre-drop-dir"
                )
            return []
        drop = Path(drop).expanduser()
        mail_root = ctx.config.mail_root if ctx.config is not None else None
        if mail_root is not None:
            try:
                assert_safe_write_paths(mail_root, drop)
            except SafetyError as exc:
                raise OutputWriterError(str(exc)) from exc
        try:
            drop.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputWriterError(
                f"Cannot create Calibre drop directory {drop}: {exc}"
            ) from exc

        stem = digest_output_stem(
            ctx.generated_at, run_id_short=ctx.run_id_short
        )
        copied: list[Path] = []
        for suffix in _COPY_SUFFIXES:
            src = ctx.output_dir / f"{stem}{suffix}"
            if not src.is_file():
                continue
            dest = drop / src.name
            try:
                shutil.copy2(src, dest)
            except OSError as exc:
                raise OutputWriterError(
                    f"Failed to copy {src.name} to {drop}: {exc}"
                ) from exc
            copied.append(dest)
        if not copied and ctx.explicit_outputs:
            raise OutputWriterError(
                "calibre writer found no briefing artifacts to copy "
                f"(looked for {stem}.* under {ctx.output_dir})"
            )
        return copied


def _resolve_drop_dir(
    args: argparse.Namespace, config: Config
) -> Path | None:
    raw = getattr(args, "calibre_drop_dir", None)
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return config.calibre.drop_dir
