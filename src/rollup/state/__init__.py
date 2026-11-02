"""SQLite state for seen undated messages and summary caches."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from rollup.cache_keys import canonicalize_provider_options
from rollup.state.migrations import (  # noqa: F401
    _ensure_schema_version_singleton,
    _existing_tables,
    _exec_ddl_statements,
    _migrate_schema_version_singleton,
    ensure_canonical_cache_tables,
    ensure_final_review_schema,
    ensure_group_summary_schema,
    ensure_message_reader_bodies_v9,
    ensure_message_reader_bodies_v10,
    ensure_reddit_catalog_v14,
    ensure_message_corpus_fts_v17,
    ensure_scholar_paper_bodies_v16,
    ensure_source_fetch_cache_v15,
    ensure_source_registry_schema,
    ensure_summaries_litellm_v11,
    ensure_web_schema,
    ensure_webpage_queue_v12,
    ensure_webpage_queue_v13,
    get_schema_version,
    refuse_unsupported_schema_version,
    run_schema_migrations,
    validate_canonical_schema,
)
from rollup.state.schema import (  # noqa: F401
    BUSY_TIMEOUT_MS,
    CANONICAL_TABLES,
    FINAL_REVIEW_SCHEMA_V5,
    GROUP_SUMMARY_BY_KEY_SCHEMA,
    GROUP_SUMMARY_SCHEMA_V6,
    MESSAGE_CORPUS_FTS_V17,
    MESSAGE_READER_BODIES_V9,
    MVP_SCHEMA,
    RATING_REASON_SEED,
    REDDIT_CATALOG_V14,
    SCHEMA_VERSION,
    SCHOLAR_PAPER_BODIES_V16,
    SOURCE_FETCH_CACHE_V15,
    SOURCE_REGISTRY_SCHEMA_V7,
    SUMMARIES_SCHEMA_V2,
    SUMMARIES_SCHEMA_V3,
    SUMMARIES_SCHEMA_V4,
    WEB_SCHEMA_V8,
    WEBPAGE_QUEUE_V12,
)


def apply_connection_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    apply_connection_pragmas(conn)
    return conn


def connect_db_mutator(db_path: Path) -> sqlite3.Connection:
    """Open an existing DB for writes without creating or migrating schema.

    Used by web POST handlers after startup ``init_db``. Fails if the file is
    missing.     Does not mkdir, migrate, or change journal mode beyond connection
    pragmas required for FK + busy timeout + WAL.
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"database does not exist: {path}")
    conn = sqlite3.connect(str(path))
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    return conn


def _sqlite_uri_path(db_path: Path) -> str:
    """Build a file: URI path component with safe encoding for unusual paths."""
    from urllib.parse import quote

    resolved = Path(db_path).expanduser().resolve()
    # Absolute POSIX path for URI; keep Windows drive paths workable via as_posix.
    posix = resolved.as_posix()
    if not posix.startswith("/"):
        posix = "/" + posix
    return quote(posix, safe="/:")


def connect_db_readonly(db_path: Path) -> sqlite3.Connection:
    """Open an existing DB read-only with query_only; never create or migrate.

    Uses URI ``mode=ro`` and ``PRAGMA query_only=ON``. Does not apply journal-mode
    or migration pragmas. Raises ``FileNotFoundError`` if the database is absent.
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"database does not exist: {path}")
    uri = f"file:{_sqlite_uri_path(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


class SchemaCompatibilityError(RuntimeError):
    """Database schema is missing, incomplete, or newer than this package."""


def assert_schema_readable(conn: sqlite3.Connection) -> int:
    """Fail closed if schema is unsupported or below the web-required floor.

    Does not migrate or repair. Returns the current schema version.
    """
    tables = _existing_tables(conn)
    if "schema_version" not in tables:
        raise SchemaCompatibilityError(
            "database has no schema_version; start rollup web once after install "
            "or run a digest so the database can be initialised"
        )
    ver = get_schema_version(conn)
    if ver > SCHEMA_VERSION:
        raise SchemaCompatibilityError(
            f"unsupported schema version {ver} (max {SCHEMA_VERSION}); "
            "upgrade the rollup package"
        )
    if ver < 8:
        raise SchemaCompatibilityError(
            f"schema version {ver} is too old for the web UI; "
            "run a digest or open the database with a current rollup to migrate"
        )
    return ver


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open or create DB and migrate to the canonical full schema shape."""
    conn = connect_db(db_path)
    refuse_unsupported_schema_version(conn)
    conn.executescript(MVP_SCHEMA)
    _ensure_schema_version_singleton(conn)
    refuse_unsupported_schema_version(conn)
    run_schema_migrations(conn)
    return conn


def init_db_with_summaries(db_path: Path) -> sqlite3.Connection:
    """Alias for init_db: schema_version always implies the full canonical shape."""
    return init_db(db_path)


def get_group_summary_generation(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
) -> str | None:
    row = conn.execute(
        "SELECT summary FROM group_summary_by_key WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if row:
        # Touch last_used_at without committing — caller owns the transaction.
        conn.execute(
            "UPDATE group_summary_by_key SET last_used_at = ? WHERE cache_key = ?",
            (datetime.now().astimezone().isoformat(), cache_key),
        )
        return row[0]
    return None


def store_group_summary_generation(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    summary: str,
    created_at: datetime,
) -> None:
    iso = created_at.isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO group_summary_by_key
           (cache_key, summary, created_at, last_used_at)
           VALUES (?, ?, ?, ?)""",
        (cache_key, summary, iso, iso),
    )
    conn.commit()


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


def get_final_review_generation(
    conn: sqlite3.Connection,
    *,
    digest_fingerprint: str,
    review_input_hash: str,
    provider: str,
    profile_name: str,
    model: str,
    prompt_version: str,
    temperature: float,
    num_ctx: int | None,
    options: dict[str, object] | None,
) -> str | None:
    options_json = canonicalize_provider_options(options)
    row = conn.execute(
        """SELECT result_json FROM final_review_generations
           WHERE digest_fingerprint = ? AND review_input_hash = ?
             AND provider = ? AND profile_name = ? AND model = ?
             AND prompt_version = ? AND temperature = ? AND num_ctx IS ?
             AND options_json = ?""",
        (
            digest_fingerprint,
            review_input_hash,
            provider,
            profile_name,
            model,
            prompt_version,
            temperature,
            num_ctx,
            options_json,
        ),
    ).fetchone()
    if row:
        return row[0]
    return None


def store_final_review_generation(
    conn: sqlite3.Connection,
    *,
    digest_fingerprint: str,
    review_input_hash: str,
    provider: str,
    profile_name: str,
    model: str,
    prompt_version: str,
    temperature: float,
    num_ctx: int | None,
    options: dict[str, object] | None,
    result_json: str,
    created_at: datetime,
) -> None:
    options_json = canonicalize_provider_options(options)
    conn.execute(
        """INSERT OR REPLACE INTO final_review_generations
           (
               digest_fingerprint, review_input_hash, provider, profile_name, model,
               prompt_version, temperature, num_ctx, options_json, result_json, created_at
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            digest_fingerprint,
            review_input_hash,
            provider,
            profile_name,
            model,
            prompt_version,
            temperature,
            num_ctx,
            options_json,
            result_json,
            created_at.isoformat(),
        ),
    )
    conn.commit()
