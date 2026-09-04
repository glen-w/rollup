"""SQLite cache for fetched Scholar paper landing pages."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from rollup.webpage.url import url_hash


def get_paper_body(conn: sqlite3.Connection, url: str) -> tuple[str, str] | None:
    """Return (title, body_text) or None."""
    digest = url_hash(url)
    row = conn.execute(
        "SELECT title, body_text FROM scholar_paper_bodies WHERE url_hash = ?",
        (digest,),
    ).fetchone()
    if not row:
        return None
    title, body = row[0], row[1]
    return (title or "", body or "")


def store_paper_body(
    conn: sqlite3.Connection,
    url: str,
    *,
    title: str,
    body_text: str,
    fetched_at: datetime,
) -> None:
    digest = url_hash(url)
    conn.execute(
        """
        INSERT INTO scholar_paper_bodies (url_hash, url, title, body_text, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(url_hash) DO UPDATE SET
            url = excluded.url,
            title = excluded.title,
            body_text = excluded.body_text,
            fetched_at = excluded.fetched_at
        """,
        (digest, url, title, body_text, fetched_at.isoformat()),
    )
    conn.commit()
