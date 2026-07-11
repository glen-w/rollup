"""Atomic filesystem helpers shared by publication and manifests."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    """Write text via a temp file in the same directory, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".tmp-{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove temp file %s: %s", tmp_path, exc)
        raise
    return path


def atomic_write_bytes(path: Path, content: bytes) -> Path:
    """Write bytes via a temp file in the same directory, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".tmp-{path.name}.",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove temp file %s: %s", tmp_path, exc)
        raise
    return path


def atomic_copy(source: Path, destination: Path) -> Path:
    """Copy source to destination atomically via a same-directory temp file."""
    source = Path(source)
    destination = Path(destination)
    return atomic_write_bytes(destination, source.read_bytes())


def publish_file_set(pairs: list[tuple[Path, Path]]) -> None:
    """Publish a set of (source, destination) copies as atomically as practical.

    Each destination is written via a temp file + rename. If any copy fails,
    previously published destinations for this call are left as-is; callers
    should validate the full set before invoking this for latest.* publication.
    """
    for source, destination in pairs:
        atomic_copy(source, destination)
