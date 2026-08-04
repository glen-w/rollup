"""Keep only the latest digest batch visible in ``output_dir`` root.

Prior dated digest artifacts are moved into ``output_dir/archive/`` before each
new write so Finder (and similar) show the current run plus ``latest.*`` and
branding — not a long history of stems.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from rollup.assets import FAVICON_FILENAME, LOGO_FILENAME
from rollup.publication import LATEST_HTML, LATEST_MD

logger = logging.getLogger(__name__)

ARCHIVE_DIRNAME = "archive"
DIGEST_MARKER = "-newsletter-digest"

# Stay in the output root across runs.
KEEP_ROOT_NAMES = frozenset(
    {
        LATEST_MD,
        LATEST_HTML,
        LOGO_FILENAME,
        FAVICON_FILENAME,
    }
)


def archive_dir(output_dir: Path) -> Path:
    return Path(output_dir) / ARCHIVE_DIRNAME


def is_archivable_output(path: Path) -> bool:
    """True for dated digest artifacts that should leave the output root."""
    if not path.is_file():
        return False
    name = path.name
    if name.startswith("."):
        return False
    if name in KEEP_ROOT_NAMES:
        return False
    return DIGEST_MARKER in name


def resolve_output_artifact(output_dir: Path, relpath: str | None) -> Path | None:
    """Resolve an indexed relative path, falling back to ``archive/``.

    Indexed runs may still store a bare filename after files were moved into
    ``archive/``. Prefer the recorded relpath; then ``archive/<basename>``.
    """
    if not relpath:
        return None
    rel = Path(relpath)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    output_dir = Path(output_dir)
    primary = (output_dir / rel).resolve()
    if primary.is_file():
        return primary
    archived = (archive_dir(output_dir) / rel.name).resolve()
    if archived.is_file():
        return archived
    return None


def archive_previous_outputs(
    output_dir: Path,
    *,
    db_path: Path | None = None,
) -> list[Path]:
    """Move prior dated digests from ``output_dir`` into ``archive/``.

    Leaves ``latest.*``, branding assets, and anything already under ``archive/``
    in place. When ``db_path`` is set, rewrites matching ``rollup_runs``
    markdown/html relpaths to ``archive/<name>`` so the web index stays valid.
    """
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return []

    dest_root = archive_dir(output_dir)
    moved: list[Path] = []
    try:
        entries = list(output_dir.iterdir())
    except OSError as exc:
        logger.warning("Could not list output_dir %s: %s", output_dir, exc)
        return []

    for path in entries:
        if path.name == ARCHIVE_DIRNAME:
            continue
        if not is_archivable_output(path):
            continue
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            dest = dest_root / path.name
            path.replace(dest)
            moved.append(dest)
        except OSError as exc:
            logger.warning("Could not archive %s → %s: %s", path, dest_root, exc)

    if moved:
        logger.info(
            "Archived %d prior digest artifact(s) into %s",
            len(moved),
            dest_root,
        )
        if db_path is not None:
            try:
                _rewrite_indexed_relpaths(db_path, {p.name for p in moved})
            except Exception as exc:
                logger.warning(
                    "Could not rewrite indexed artifact paths after archive: %s",
                    exc,
                )
    return moved


def _rewrite_indexed_relpaths(db_path: Path, moved_names: set[str]) -> int:
    """Point rollup_runs relpaths at archive/ for files that just moved."""
    if not moved_names:
        return 0
    db_path = Path(db_path)
    if not db_path.is_file():
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rollup_runs'"
        )
        if cur.fetchone() is None:
            return 0

        updated = 0
        for name in moved_names:
            archived_rel = f"{ARCHIVE_DIRNAME}/{name}"
            for column in ("markdown_relpath", "html_relpath"):
                result = conn.execute(
                    f"""UPDATE rollup_runs
                       SET {column} = ?
                       WHERE {column} = ?
                          OR {column} = ?""",
                    (archived_rel, name, f"./{name}"),
                )
                updated += int(result.rowcount or 0)
        conn.commit()
        if updated:
            logger.info(
                "Rewrote %d indexed artifact path(s) into %s/",
                updated,
                ARCHIVE_DIRNAME,
            )
        return updated
    finally:
        conn.close()
