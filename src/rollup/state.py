"""SQLite state for seen undated messages and summary cache."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


SCHEMA_VERSION = 1

MVP_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_messages (
    message_key TEXT PRIMARY KEY,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""

SUMMARIES_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS summaries (
    message_key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    newsletter_type TEXT NOT NULL,
    model TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MVP_SCHEMA)
    conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn


def init_db_with_summaries(db_path: Path) -> sqlite3.Connection:
    conn = init_db(db_path)
    conn.executescript(SUMMARIES_SCHEMA_V2)
    conn.commit()
    return conn


def load_seen_keys(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT message_key FROM seen_messages").fetchall()
    return {row[0] for row in rows}


def upsert_seen_keys(
    conn: sqlite3.Connection, keys: list[str], seen_at: datetime
) -> None:
    if not keys:
        return
    iso = seen_at.isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO seen_messages (message_key, last_seen_at) VALUES (?, ?)",
        [(k, iso) for k in keys],
    )
    conn.commit()


def get_cached_summary(
    conn: sqlite3.Connection, message_key: str, content_hash: str
) -> str | None:
    row = conn.execute(
        "SELECT summary, content_hash FROM summaries WHERE message_key = ?",
        (message_key,),
    ).fetchone()
    if row and row[1] == content_hash:
        return row[0]
    return None


def store_summary(
    conn: sqlite3.Connection,
    message_key: str,
    content_hash: str,
    newsletter_type: str,
    model: str,
    summary: str,
    created_at: datetime,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO summaries
           (message_key, content_hash, newsletter_type, model, summary, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (message_key, content_hash, newsletter_type, model, summary, created_at.isoformat()),
    )
    conn.commit()
