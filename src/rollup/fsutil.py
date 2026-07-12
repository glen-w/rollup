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
    """Publish a set of (source, destination) copies as an all-or-nothing set.

    Writes every destination via a same-directory temp file first, then renames
    all temps into place. If any stage fails, temps are removed and previously
    replaced destinations for this call are restored from backups when possible,
    so latest.md and latest.html cannot point at different runs.
    """
    staged: list[tuple[Path, Path, Path | None]] = []
    # (tmp_path, destination, backup_path|None)
    try:
        for source, destination in pairs:
            source = Path(source)
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".tmp-{destination.name}.",
                dir=str(destination.parent),
            )
            tmp_path = Path(tmp_name)
            backup_path: Path | None = None
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(source.read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                if destination.exists():
                    backup_path = destination.with_name(
                        f".bak-{destination.name}.{os.getpid()}"
                    )
                    destination.replace(backup_path)
                staged.append((tmp_path, destination, backup_path))
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Could not remove temp file %s: %s", tmp_path, exc)
                if backup_path is not None and backup_path.exists():
                    try:
                        backup_path.replace(destination)
                    except OSError as exc:
                        logger.warning(
                            "Could not restore backup %s: %s", backup_path, exc
                        )
                raise
        # Commit: rename all temps into final destinations.
        for tmp_path, destination, _backup in staged:
            tmp_path.replace(destination)
        # Drop backups after successful commit.
        for _tmp, _dest, backup_path in staged:
            if backup_path is not None and backup_path.exists():
                try:
                    backup_path.unlink()
                except OSError as exc:
                    logger.warning("Could not remove backup %s: %s", backup_path, exc)
    except Exception:
        # Roll back any committed renames and restore backups; remove temps.
        for tmp_path, destination, backup_path in reversed(staged):
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temp file %s: %s", tmp_path, exc)
            if backup_path is not None and backup_path.exists():
                try:
                    if destination.exists():
                        destination.unlink(missing_ok=True)
                    backup_path.replace(destination)
                except OSError as exc:
                    logger.warning(
                        "Could not restore backup %s → %s: %s",
                        backup_path,
                        destination,
                        exc,
                    )
        raise

