"""Post-digest output writer plugin seam.

Default MD/HTML stay in core. Named addons (and third-party entry points) attach
after ``DigestReport`` is built and write additional artifacts.

When ``--output`` / ``--xteink`` are omitted, every discovered writer runs. Pass
``--output none`` (or sticky ``output = "none"``) for Markdown/HTML only.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable

from rollup.config import Config
from rollup.models import DigestReport

if TYPE_CHECKING:
    from rollup.folder_theme import FolderThemeOverride

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "rollup.output_writers"
OUTPUT_NONE = "none"


class OutputWriterError(RuntimeError):
    """Raised when an output writer cannot be discovered or run."""


@dataclass(frozen=True)
class WriteContext:
    """Shared write environment for output writers."""

    output_dir: Path
    generated_at: datetime
    max_display_links: int
    dry_run: bool
    run_id_short: str | None = None
    logger: logging.Logger | None = None
    # False when writers were enabled by the default-all policy (not explicit flags).
    explicit_outputs: bool = True
    folder_themes: Mapping[str, "FolderThemeOverride"] | None = None


@runtime_checkable
class OutputWriter(Protocol):
    """Addon that writes extra digest artifacts from a finished report."""

    name: str

    def register_cli(self, parser: argparse.ArgumentParser) -> None:
        """Register optional writer-specific flags (beyond ``--output``)."""

    def enabled(self, args: argparse.Namespace, config: Config) -> bool:
        """Return True when this writer should run for the current invocation."""

    def write(self, report: DigestReport, ctx: WriteContext) -> list[Path]:
        """Write artifacts; return paths written. Skip I/O when ``ctx.dry_run``."""


def _raw_output_tokens(args: argparse.Namespace) -> list[str]:
    tokens: list[str] = []
    for raw in getattr(args, "output", None) or []:
        cleaned = str(raw).strip().lower()
        if cleaned:
            tokens.append(cleaned)
    return tokens


def _normalize_writer_token(token: str) -> str:
    """Map legacy aliases onto canonical writer names."""
    if token == "x3":
        return "xteink"
    return token


def requested_writer_names(args: argparse.Namespace) -> set[str] | None:
    """Normalize ``--output`` / ``--xteink`` into writer names.

    Returns:
        ``None`` — no selection; run every discovered writer (default).
        empty set — ``--output none``; Markdown/HTML only.
        non-empty set — explicit writer list.
    """
    tokens = [_normalize_writer_token(t) for t in _raw_output_tokens(args)]
    has_none = OUTPUT_NONE in tokens
    named = {t for t in tokens if t != OUTPUT_NONE}
    if getattr(args, "xteink", False) or getattr(args, "x3", False):
        named.add("xteink")

    if has_none and named:
        # Validated separately; treat as explicit named set for enablement.
        return named
    if has_none:
        return set()
    if not named and not tokens:
        return None
    return named


def validate_output_none_mix(args: argparse.Namespace) -> str | None:
    """Reject combining ``--output none`` with other writer names or ``--xteink``."""
    tokens = [_normalize_writer_token(t) for t in _raw_output_tokens(args)]
    if OUTPUT_NONE not in tokens:
        return None
    named = {t for t in tokens if t != OUTPUT_NONE}
    if named or getattr(args, "xteink", False) or getattr(args, "x3", False):
        return (
            f"Cannot combine --output {OUTPUT_NONE} with other --output names "
            "or --xteink"
        )
    return None


def builtin_writers() -> dict[str, OutputWriter]:
    """In-tree output writers loaded without entry points."""
    from rollup.addons.epub import EpubOutputWriter
    from rollup.addons.json import JsonOutputWriter
    from rollup.addons.txt import TxtOutputWriter
    from rollup.addons.xteink import XteinkOutputWriter

    return {
        "xteink": XteinkOutputWriter(),
        "epub": EpubOutputWriter(),
        "json": JsonOutputWriter(),
        "txt": TxtOutputWriter(),
    }


def _load_entry_point_writers() -> dict[str, OutputWriter]:
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return {}

    eps = entry_points()
    if hasattr(eps, "select"):
        selected = list(eps.select(group=ENTRY_POINT_GROUP))
    else:  # pragma: no cover - Python <3.12 dict API
        selected = list(eps.get(ENTRY_POINT_GROUP, []))  # type: ignore[arg-type]

    loaded: dict[str, OutputWriter] = {}
    for ep in selected:
        name = ep.name.strip().lower()
        if not name:
            raise OutputWriterError(f"Empty output writer entry point name: {ep.value!r}")
        if name in loaded:
            raise OutputWriterError(
                f"Duplicate output writer entry point name {name!r}"
            )
        obj = ep.load()
        writer = obj() if isinstance(obj, type) else obj
        if not isinstance(writer, OutputWriter) and not hasattr(writer, "write"):
            raise OutputWriterError(
                f"Entry point {name!r} ({ep.value!r}) is not a valid OutputWriter"
            )
        if getattr(writer, "name", name) != name:
            raise OutputWriterError(
                f"Entry point name {name!r} does not match writer.name "
                f"{getattr(writer, 'name', None)!r}"
            )
        loaded[name] = writer
    return loaded


def discover_writers() -> dict[str, OutputWriter]:
    """Load builtin writers, then entry-point writers.

    Duplicate names between builtins and entry points are rejected.
    """
    writers = dict(builtin_writers())
    for name, writer in _load_entry_point_writers().items():
        if name in writers:
            raise OutputWriterError(
                f"Duplicate output writer name {name!r}: entry point conflicts "
                f"with a built-in writer"
            )
        writers[name] = writer
    return writers


def validate_requested_writers(
    args: argparse.Namespace,
    writers: Mapping[str, OutputWriter],
) -> str | None:
    """Return an error message if ``--output`` / ``--xteink`` names unknown writers."""
    mix_err = validate_output_none_mix(args)
    if mix_err:
        return mix_err
    requested = requested_writer_names(args)
    if requested is None:
        return None
    unknown = sorted(requested - set(writers))
    if not unknown:
        return None
    available = ", ".join(sorted(writers)) or "(none)"
    return (
        f"Unknown output writer(s): {', '.join(unknown)}. "
        f"Available: {available} (or '{OUTPUT_NONE}' for Markdown/HTML only)"
    )


def register_writer_cli(
    parser: argparse.ArgumentParser,
    writers: Mapping[str, OutputWriter] | None = None,
) -> None:
    """Let each discovered writer register optional CLI flags."""
    for writer in (writers or discover_writers()).values():
        writer.register_cli(parser)


def run_enabled_writers(
    writers: Mapping[str, OutputWriter],
    report: DigestReport,
    ctx: WriteContext,
    *,
    args: argparse.Namespace,
    config: Config,
) -> list[Path]:
    """Run every enabled writer sequentially; raise on the first failure."""
    log = ctx.logger or logger
    requested = requested_writer_names(args)
    explicit = requested is not None
    # Rebuild context with explicit_outputs reflecting selection policy.
    ctx = WriteContext(
        output_dir=ctx.output_dir,
        generated_at=ctx.generated_at,
        max_display_links=ctx.max_display_links,
        dry_run=ctx.dry_run,
        run_id_short=ctx.run_id_short,
        logger=ctx.logger,
        explicit_outputs=explicit,
        folder_themes=ctx.folder_themes,
    )
    written: list[Path] = []
    for name, writer in writers.items():
        if not writer.enabled(args, config):
            continue
        if ctx.dry_run:
            log.info("Dry run — skipping output writer %s", name)
            continue
        log.info("Running output writer %s...", name)
        try:
            paths = list(writer.write(report, ctx))
        except Exception as exc:
            raise OutputWriterError(
                f"Output writer {name!r} failed: {exc}"
            ) from exc
        written.extend(paths)
        for path in paths:
            log.info("Wrote %s", path)
    return written
