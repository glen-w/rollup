"""SQLite CRUD for the Reddit subscription catalog."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from rollup.reddit.models import RedditCatalogEntry

_ROW_SELECT = """
SELECT name, title, over_18, fetched_at
FROM reddit_sub_catalog
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_row(row: tuple) -> RedditCatalogEntry:
    return RedditCatalogEntry(
        name=row[0],
        title=row[1],
        over_18=bool(row[2]),
        fetched_at=_parse_dt(row[3]) or datetime.now(timezone.utc),
    )


def list_catalog(
    conn: sqlite3.Connection,
    *,
    limit: int = 500,
) -> tuple[RedditCatalogEntry, ...]:
    rows = conn.execute(
        f"{_ROW_SELECT} ORDER BY name ASC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return tuple(_parse_row(r) for r in rows)


def replace_catalog(
    conn: sqlite3.Connection,
    entries: tuple[RedditCatalogEntry, ...],
) -> None:
    conn.execute("DELETE FROM reddit_sub_catalog")
    for entry in entries:
        conn.execute(
            """
            INSERT INTO reddit_sub_catalog (name, title, over_18, fetched_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                entry.name,
                entry.title,
                int(entry.over_18),
                entry.fetched_at.isoformat(),
            ),
        )


def catalog_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM reddit_sub_catalog").fetchone()
    return int(row[0]) if row else 0
