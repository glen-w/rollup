"""Discover Thunderbird mbox files under a newsletter root."""

from __future__ import annotations

import logging
import mailbox
from pathlib import Path
from typing import Iterator

from rollup.models import InventoryEntry, MboxFolder

logger = logging.getLogger(__name__)

# Thunderbird sidecar / non-mbox names (exact basename match, case-sensitive as on disk).
_EXCLUDED_BASENAMES = frozenset(
    {
        "msgFilterRules.dat",
        "filterlog.html",
        "popstate.dat",
        "folderCache.json",
    }
)

# Suffixes that are never mbox content (checked case-insensitively on the final suffix).
_EXCLUDED_SUFFIXES = frozenset(
    {
        ".msf",
        ".dat",
        ".html",
        ".json",
        ".bak",
        ".tmp",
        ".part",
        ".toc",
    }
)


def _is_excluded_sidecar(name: str) -> bool:
    if name in _EXCLUDED_BASENAMES:
        return True
    if name.startswith("."):
        return True
    lower = name.lower()
    for suffix in _EXCLUDED_SUFFIXES:
        if lower.endswith(suffix):
            return True
    return False


def _is_sbd_dir(entry: Path) -> bool:
    """True for Thunderbird .sbd directory containers (not via Path.suffix alone)."""
    return entry.name.lower().endswith(".sbd")


def _derive_folder_name(mbox_path: Path, newsletter_root: Path) -> tuple[str, str]:
    """Preserve exact mbox basename; strip only trailing .sbd from directory parts."""
    rel = mbox_path.relative_to(newsletter_root)
    parts = list(rel.parts)
    cleaned: list[str] = []
    for part in parts[:-1]:
        if part.lower().endswith(".sbd"):
            cleaned.append(part[: -len(".sbd")])
        else:
            cleaned.append(part)
    if parts:
        cleaned.append(parts[-1])  # exact basename — supports AI.News etc.
    relative_path = "/".join(cleaned)
    return relative_path, relative_path


def iter_mbox_files(newsletter_root: Path) -> Iterator[MboxFolder]:
    """Recursively find Thunderbird mbox files under newsletter_root.

    Mbox identity is an extensionless-looking leaf that is not an excluded sidecar.
    Names containing periods (e.g. ``AI.News``) are valid. Directory symlinks are
    rejected; resolved directories are tracked to prevent cycles and duplicates.
    """
    newsletter_root = newsletter_root.resolve()
    seen_dirs: set[Path] = set()

    def _walk(directory: Path) -> Iterator[MboxFolder]:
        try:
            resolved_dir = directory.resolve()
        except OSError as exc:
            logger.error("Cannot resolve directory %s: %s", directory, exc)
            return
        if resolved_dir in seen_dirs:
            return
        if not is_path_inside(resolved_dir, newsletter_root):
            logger.error(
                "Refusing to traverse outside newsletter root: %s", resolved_dir
            )
            return
        seen_dirs.add(resolved_dir)

        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            logger.error("Cannot read directory %s: %s", directory, exc)
            return

        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_symlink():
                    # Reject all directory symlinks; never follow .sbd links out.
                    if entry.is_dir():
                        logger.error(
                            "Rejecting directory symlink during discovery: %s", entry
                        )
                        continue
                    # File symlinks to mboxes are also rejected for containment safety.
                    logger.error("Rejecting file symlink during discovery: %s", entry)
                    continue
            except OSError as exc:
                logger.error("Cannot stat %s: %s", entry, exc)
                continue

            if entry.is_dir() and _is_sbd_dir(entry):
                yield from _walk(entry)
                continue
            if entry.is_dir():
                continue
            if not entry.is_file():
                continue
            if _is_excluded_sidecar(entry.name):
                continue
            # Mbox: regular file that is not an excluded sidecar. Do not use
            # Path.suffix == "" (breaks AI.News) or Path.stem for identity.
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


def is_path_inside(child: Path, parent: Path) -> bool:
    """Containment helper local to discovery (resolved paths)."""
    child_r = child.resolve()
    parent_r = parent.resolve()
    if child_r == parent_r:
        return True
    try:
        child_r.relative_to(parent_r)
        return True
    except ValueError:
        return False


def list_flat_mbox_names(
    newsletter_root: Path | None,
    *,
    include: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """List top-level mbox basenames under a Thunderbird newsletter root.

    Used by the web UI (Settings folder themes, Run Studio matched folders).
    Skips dotfiles, ``.sbd`` directories, and Thunderbird sidecars via
    ``_is_excluded_sidecar``. Optional ``include`` / ``exclude`` filters are
    case-insensitive on folder names.
    """
    if newsletter_root is None or not newsletter_root.is_dir():
        return []
    include_list = list(include)
    exclude_list = list(exclude)
    include_lower = {f.lower() for f in include_list} if include_list else None
    exclude_lower = {f.lower() for f in exclude_list}
    names: list[str] = []
    try:
        for child in sorted(newsletter_root.iterdir()):
            if child.is_dir() and _is_sbd_dir(child):
                continue
            if not child.is_file():
                continue
            if _is_excluded_sidecar(child.name):
                continue
            name = child.name
            if include_lower is not None:
                if name not in include_list and name.lower() not in include_lower:
                    continue
            if name in exclude_list or name.lower() in exclude_lower:
                continue
            names.append(name)
    except OSError:
        return []
    return names


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


def list_linkedin_folder_names(
    linkedin_config,
    *,
    include: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Return linkedin folder names for enabled searches."""
    from rollup.linkedin.config import LINKEDIN_FEED_FOLDER, filter_linkedin_searches

    if linkedin_config is None:
        return []
    searches = filter_linkedin_searches(
        linkedin_config.searches,
        folders_include=tuple(include),
        folders_exclude=tuple(exclude),
        layout=linkedin_config.layout,
    )
    if not searches:
        return []
    if linkedin_config.layout == "feed":
        return [LINKEDIN_FEED_FOLDER]
    if linkedin_config.layout == "per_search":
        return [s.folder_name for s in searches]
    # per_source: folder names depend on fetched authors; expose feed as fallback
    return [LINKEDIN_FEED_FOLDER]


def list_webpage_folder_names(
    *,
    pending_count: int | None = None,
    item_count: int | None = None,
    include: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Return webpage:queue when saved/pending items exist and folder filters allow it."""
    from rollup.webpage.config import WEBPAGE_FOLDER_NAME

    visible = item_count if item_count is not None else (pending_count or 0)
    if visible <= 0:
        return []
    include_set = set(include)
    exclude_set = set(exclude)
    if WEBPAGE_FOLDER_NAME in exclude_set:
        return []
    if include_set and WEBPAGE_FOLDER_NAME not in include_set:
        return []
    return [WEBPAGE_FOLDER_NAME]


def list_reddit_folder_names(
    reddit_config,
    *,
    include: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
) -> list[str]:
    from rollup.reddit.config import list_reddit_folder_names as _list

    return _list(reddit_config, include=include, exclude=exclude)


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
