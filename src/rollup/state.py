"""SQLite state for seen undated messages and summary cache."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


SCHEMA_VERSION = 2

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
    message_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    newsletter_type TEXT NOT NULL,
    model TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (message_key, content_hash, newsletter_type, model)
);
"""

_SUMMARIES_COMPOSITE_PK = "primary key (message_key, content_hash, newsletter_type, model)"


def _summaries_needs_migration(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='summaries'"
    ).fetchone()
    if not row or not row[0]:
        return False
    normalized = " ".join(row[0].lower().split())
    return _SUMMARIES_COMPOSITE_PK not in normalized


def _migrate_summaries_schema(conn: sqlite3.Connection) -> None:
    if not _summaries_needs_migration(conn):
        return
    conn.executescript(
        """
        CREATE TABLE summaries_migrated (
            message_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            newsletter_type TEXT NOT NULL,
            model TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_key, content_hash, newsletter_type, model)
        );
        INSERT INTO summaries_migrated
            SELECT message_key, content_hash, newsletter_type, model, summary, created_at
            FROM summaries;
        DROP TABLE summaries;
        ALTER TABLE summaries_migrated RENAME TO summaries;
        """
    )
    conn.commit()


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
    _migrate_summaries_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
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
    conn: sqlite3.Connection,
    message_key: str,
    content_hash: str,
    model: str,
    newsletter_type: str,
) -> str | None:
    row = conn.execute(
        """SELECT summary FROM summaries
           WHERE message_key = ? AND content_hash = ?
             AND model = ? AND newsletter_type = ?""",
        (message_key, content_hash, model, newsletter_type),
    ).fetchone()
    if row:
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
