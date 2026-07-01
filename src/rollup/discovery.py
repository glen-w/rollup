"""Discover Thunderbird mbox files under a newsletter root."""

from __future__ import annotations

import logging
import mailbox
from pathlib import Path
from typing import Iterator

from rollup.models import InventoryEntry, MboxFolder

logger = logging.getLogger(__name__)


def _derive_folder_name(mbox_path: Path, newsletter_root: Path) -> tuple[str, str]:
    rel = mbox_path.relative_to(newsletter_root)
    parts = list(rel.parts)
    if parts:
        parts[-1] = Path(parts[-1]).stem  # strip any extension (none for mbox)
    # Strip .sbd from directory components only
    cleaned: list[str] = []
    for i, part in enumerate(parts[:-1]):
        cleaned.append(part[:-4] if part.endswith(".sbd") else part)
    if parts:
        cleaned.append(parts[-1])
    relative_path = "/".join(cleaned)
    folder_name = relative_path
    return folder_name, relative_path


def iter_mbox_files(newsletter_root: Path) -> Iterator[MboxFolder]:
    """Recursively find extensionless mbox files under newsletter_root."""
    newsletter_root = newsletter_root.resolve()

    def _walk(directory: Path) -> Iterator[MboxFolder]:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            logger.error("Cannot read directory %s: %s", directory, exc)
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and entry.suffix == ".sbd":
                yield from _walk(entry)
                continue
            if entry.is_file() and entry.suffix == ".msf":
                continue
            if entry.is_file() and entry.suffix == "":
                folder_name, relative_path = _derive_folder_name(entry, newsletter_root)
                try:
                    size_bytes = entry.stat().st_size
                except OSError:
                    size_bytes = 0
                yield MboxFolder(
                    folder_name=folder_name,
                    relative_path=relative_path,
                    mbox_path=entry,
                    size_bytes=size_bytes,
                )

    yield from _walk(newsletter_root)


def filter_folders(
    folders: list[MboxFolder],
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> list[MboxFolder]:
    """Filter discovered folders by name."""
    result = folders
    if include:
        include_set = set(include)
        result = [f for f in result if f.folder_name in include_set]
    if exclude:
        exclude_set = set(exclude)
        result = [f for f in result if f.folder_name not in exclude_set]
    return result


def count_messages_fast(mbox_path: Path) -> tuple[int | None, str | None]:
    """Count messages without parsing bodies."""
    try:
        mbox = mailbox.mbox(str(mbox_path), create=False)
        try:
            count = len(mbox)
            return count, None
        finally:
            mbox.close()
    except Exception as exc:
        return None, str(exc)


def build_inventory(newsletter_root: Path) -> list[InventoryEntry]:
    """Build inventory of all mbox folders under root."""
    entries: list[InventoryEntry] = []
    for folder in iter_mbox_files(newsletter_root):
        count, error = count_messages_fast(folder.mbox_path)
        entries.append(
            InventoryEntry(folder=folder, message_count=count, parse_error=error)
        )
    return entries
