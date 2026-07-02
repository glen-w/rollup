"""SQLite state for seen undated messages and summary caches."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from rollup.cache_keys import canonicalize_provider_options

SCHEMA_VERSION = 4

MVP_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_messages (
    message_key TEXT PRIMARY KEY,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
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

SUMMARIES_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS summary_generations (
    message_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    newsletter_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_style TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    temperature REAL NOT NULL,
    num_ctx INTEGER,
    options_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        message_key,
        content_hash,
        newsletter_type,
        provider,
        profile_name,
        model,
        prompt_style,
        prompt_version,
        temperature,
        num_ctx,
        options_json
    )
);
"""

SUMMARIES_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS summary_generations (
    message_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    newsletter_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_style TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    temperature REAL NOT NULL,
    num_ctx INTEGER,
    options_json TEXT NOT NULL,
    summary_input_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        message_key,
        content_hash,
        newsletter_type,
        provider,
        profile_name,
        model,
        prompt_style,
        prompt_version,
        temperature,
        num_ctx,
        options_json,
        summary_input_hash
    )
);
"""

_SUMMARIES_COMPOSITE_PK = (
    "primary key (message_key, content_hash, newsletter_type, model)"
)
_SUMMARY_GENERATIONS_INPUT_HASH_PK = "summary_input_hash"


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
    conn.executescript("""
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
        """)
    conn.commit()


def _summary_generations_needs_v4_migration(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='summary_generations'"
    ).fetchone()
    if not row or not row[0]:
        return False
    normalized = " ".join(row[0].lower().split())
    return _SUMMARY_GENERATIONS_INPUT_HASH_PK not in normalized


def _migrate_summary_generations_v4(conn: sqlite3.Connection) -> None:
    if not _summary_generations_needs_v4_migration(conn):
        return
    conn.executescript("""
        DROP TABLE IF EXISTS summary_generations;
        """)
    conn.executescript(SUMMARIES_SCHEMA_V4)
    conn.commit()


def _schema_version_table_info(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("PRAGMA table_info(schema_version)").fetchall()


def _migrate_schema_version_singleton(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in _schema_version_table_info(conn)}
    if not columns:
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_version (id, version) VALUES (1, ?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        return
    if "id" in columns:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, ?)",
            (SCHEMA_VERSION,),
        )
        conn.execute(
            "UPDATE schema_version SET version = ? WHERE id = 1",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        return
    current_row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current_version = int(current_row[0] or 0)
    conn.execute("DROP TABLE schema_version")
    conn.execute(
        "CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_version (id, version) VALUES (1, ?)",
        (max(current_version, SCHEMA_VERSION),),
    )
    conn.commit()


def _set_schema_version(conn: sqlite3.Connection) -> None:
    _migrate_schema_version_singleton(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, ?)",
        (SCHEMA_VERSION,),
    )
    conn.execute(
        "UPDATE schema_version SET version = ? WHERE id = 1",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current database schema version."""
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    if row is None:
        return 0
    return int(row[0])


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MVP_SCHEMA)
    _set_schema_version(conn)
    return conn


def init_db_with_summaries(db_path: Path) -> sqlite3.Connection:
    conn = init_db(db_path)
    conn.executescript(SUMMARIES_SCHEMA_V2)
    conn.executescript(SUMMARIES_SCHEMA_V3)
    _migrate_summaries_schema(conn)
    _migrate_summary_generations_v4(conn)
    conn.executescript(SUMMARIES_SCHEMA_V4)
    _set_schema_version(conn)
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


def get_cached_summary_generation(
    conn: sqlite3.Connection,
    *,
    message_key: str,
    content_hash: str,
    newsletter_type: str,
    provider: str,
    profile_name: str,
    model: str,
    prompt_style: str,
    prompt_version: int,
    temperature: float,
    num_ctx: int | None,
    options: dict[str, object] | None,
    summary_input_hash: str,
) -> str | None:
    options_json = canonicalize_provider_options(options)
    row = conn.execute(
        """SELECT summary FROM summary_generations
           WHERE message_key = ? AND content_hash = ? AND newsletter_type = ?
             AND provider = ? AND profile_name = ? AND model = ? AND prompt_style = ?
             AND prompt_version = ? AND temperature = ? AND num_ctx IS ?
             AND options_json = ? AND summary_input_hash = ?""",
        (
            message_key,
            content_hash,
            newsletter_type,
            provider,
            profile_name,
            model,
            prompt_style,
            prompt_version,
            temperature,
            num_ctx,
            options_json,
            summary_input_hash,
        ),
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
        (
            message_key,
            content_hash,
            newsletter_type,
            model,
            summary,
            created_at.isoformat(),
        ),
    )
    conn.commit()


def store_summary_generation(
    conn: sqlite3.Connection,
    *,
    message_key: str,
    content_hash: str,
    newsletter_type: str,
    provider: str,
    profile_name: str,
    model: str,
    prompt_style: str,
    prompt_version: int,
    temperature: float,
    num_ctx: int | None,
    options: dict[str, object] | None,
    summary_input_hash: str,
    summary: str,
    created_at: datetime,
) -> None:
    options_json = canonicalize_provider_options(options)
    conn.execute(
        """INSERT OR REPLACE INTO summary_generations
           (
               message_key, content_hash, newsletter_type, provider, profile_name, model,
               prompt_style, prompt_version, temperature, num_ctx, options_json,
               summary_input_hash, summary, created_at
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_key,
            content_hash,
            newsletter_type,
            provider,
            profile_name,
            model,
            prompt_style,
            prompt_version,
            temperature,
            num_ctx,
            options_json,
            summary_input_hash,
            summary,
            created_at.isoformat(),
        ),
    )
    conn.commit()
