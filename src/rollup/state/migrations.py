"""Ordered schema migrations for rollup.db (file-move; bodies unchanged)."""

from __future__ import annotations

import sqlite3

from rollup.state.schema import *  # noqa: F403
from rollup.state.schema import (  # noqa: F401
    MESSAGE_CORPUS_FTS_V17,
    _SUMMARIES_COMPOSITE_PK,
    _SUMMARY_GENERATIONS_INPUT_HASH_PK,
    _V7_REQUIRED_TABLES,
    _V9_REQUIRED_COLUMNS,
    _V10_REQUIRED_COLUMNS,
    _V13_WEBPAGE_COLUMNS,
    _V13_WEBPAGE_COLUMN_NAMES,
)


def apply_connection_pragmas(conn: sqlite3.Connection) -> None:
    """Delegate to the package facade so busy-timeout stays patchable."""
    from rollup.state import apply_connection_pragmas as apply_pragmas

    apply_pragmas(conn)


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def validate_canonical_schema(conn: sqlite3.Connection) -> None:
    """Validate the final current-shape catalogue after migrations/init."""
    existing = _existing_tables(conn)
    missing = sorted(CANONICAL_TABLES - existing)
    if missing:
        raise sqlite3.DatabaseError(
            f"schema corruption or incomplete migration: missing tables {missing}"
        )
    _validate_reader_bodies_shape(conn, required=_V10_REQUIRED_COLUMNS)
    if not _v7_shape_complete(conn):
        raise sqlite3.DatabaseError(
            "schema corruption: source registry tables incomplete for canonical shape"
        )
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise sqlite3.DatabaseError(
            f"foreign_key_check failed for canonical schema: {fk_errors}"
        )


def _v7_shape_complete(conn: sqlite3.Connection) -> bool:
    existing = _existing_tables(conn)
    if not _V7_REQUIRED_TABLES.issubset(existing):
        return False
    required_source_cols = {
        "source_key",
        "identity_version",
        "lifecycle",
        "created_at",
        "updated_at",
    }
    return required_source_cols.issubset(_table_columns(conn, "sources"))


def refuse_unsupported_schema_version(conn: sqlite3.Connection) -> None:
    """Refuse DBs newer than this code before any mutate.

    Safe when schema_version is absent (treated as 0).
    """
    tables = _existing_tables(conn)
    if "schema_version" not in tables:
        return
    ver = get_schema_version(conn)
    if ver > SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            f"unsupported schema version {ver} (max {SCHEMA_VERSION}); "
            "refusing to modify database"
        )


def _summaries_needs_migration(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='summaries'"
    ).fetchone()
    if not row or not row[0]:
        return False
    normalized = " ".join(row[0].lower().split())
    return _SUMMARIES_COMPOSITE_PK not in normalized


def _migrate_summaries_schema(conn: sqlite3.Connection) -> None:
    """Rebuild summaries PK inside one IMMEDIATE transaction."""
    if not _summaries_needs_migration(conn):
        return
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(
            conn,
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
            """,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _summary_generations_needs_v4_migration(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='summary_generations'"
    ).fetchone()
    if not row or not row[0]:
        return False
    normalized = " ".join(row[0].lower().split())
    return _SUMMARY_GENERATIONS_INPUT_HASH_PK not in normalized


def _migrate_summary_generations_v4(conn: sqlite3.Connection) -> None:
    """Replace summary_generations with v4 shape inside one IMMEDIATE transaction."""
    if not _summary_generations_needs_v4_migration(conn):
        return
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS summary_generations")
        _exec_ddl_statements(conn, SUMMARIES_SCHEMA_V4)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _schema_version_table_info(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("PRAGMA table_info(schema_version)").fetchall()


def _ensure_schema_version_singleton(conn: sqlite3.Connection) -> None:
    """Ensure singleton schema_version row exists without advancing or lowering version.

    Fresh DBs start at version 0; migrations bump version only inside their own
    committed transactions. Never writes SCHEMA_VERSION here.
    """
    refuse_unsupported_schema_version(conn)
    columns = {row[1] for row in _schema_version_table_info(conn)}
    if not columns:
        conn.execute(
            "CREATE TABLE schema_version "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 0)")
        conn.commit()
        return
    if "id" in columns:
        # Preserve existing version; only insert a zero row when missing.
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)"
        )
        conn.commit()
        refuse_unsupported_schema_version(conn)
        return
    # Legacy multi-row table → singleton, preserving max version (never raise to current).
    refuse_unsupported_schema_version(conn)
    current_row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current_version = int(current_row[0] or 0)
    if current_version > SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            f"unsupported schema version {current_version} (max {SCHEMA_VERSION}); "
            "refusing to modify database"
        )
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE schema_version")
        conn.execute(
            "CREATE TABLE schema_version "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_version (id, version) VALUES (1, ?)",
            (current_version,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# Back-compat alias used by older tests/callers.
_migrate_schema_version_singleton = _ensure_schema_version_singleton


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current database schema version."""
    tables = _existing_tables(conn)
    if "schema_version" not in tables:
        return 0
    cols = {row[1] for row in _schema_version_table_info(conn)}
    if "id" not in cols:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0] or 0) if row else 0
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _bump_schema_version_in_txn(conn: sqlite3.Connection, version: int) -> None:
    """Monotonic version bump; caller owns the surrounding IMMEDIATE transaction."""
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)"
    )
    conn.execute(
        "UPDATE schema_version SET version = ? WHERE id = 1 AND version < ?",
        (version, version),
    )


def ensure_final_review_schema(conn: sqlite3.Connection) -> None:
    refuse_unsupported_schema_version(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, FINAL_REVIEW_SCHEMA_V5)
        _bump_schema_version_in_txn(conn, 5)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_group_summary_schema(conn: sqlite3.Connection) -> None:
    """Additive schema v6: group summary caches. Preserves all prior tables."""
    refuse_unsupported_schema_version(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, GROUP_SUMMARY_SCHEMA_V6)
        _exec_ddl_statements(conn, GROUP_SUMMARY_BY_KEY_SCHEMA)
        _bump_schema_version_in_txn(conn, 6)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _bump_schema_version_at_least(conn: sqlite3.Connection, version: int) -> None:
    refuse_unsupported_schema_version(conn)
    _ensure_schema_version_singleton(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _bump_schema_version_in_txn(conn, version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _exec_ddl_statements(conn: sqlite3.Connection, script: str) -> None:
    """Execute DDL without executescript's implicit commit (safe inside a txn)."""
    for stmt in script.split(";"):
        text = stmt.strip()
        if text:
            conn.execute(text)


def ensure_canonical_cache_tables(conn: sqlite3.Connection) -> None:
    """Create empty summary/final-review/group tables when missing (no version lie)."""
    refuse_unsupported_schema_version(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, SUMMARIES_SCHEMA_V2)
        if "summary_generations" not in _existing_tables(conn):
            _exec_ddl_statements(conn, SUMMARIES_SCHEMA_V4)
        _exec_ddl_statements(conn, FINAL_REVIEW_SCHEMA_V5)
        _exec_ddl_statements(conn, GROUP_SUMMARY_SCHEMA_V6)
        _exec_ddl_statements(conn, GROUP_SUMMARY_BY_KEY_SCHEMA)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _migrate_summaries_schema(conn)
    _migrate_summary_generations_v4(conn)


def ensure_source_registry_schema(conn: sqlite3.Connection) -> None:
    """Atomic schema v7: source registry tables. Rollback leaves valid prior DB."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    if get_schema_version(conn) >= 7 and _v7_shape_complete(conn):
        return
    if get_schema_version(conn) >= 7 and not _v7_shape_complete(conn):
        # Narrow repair: version ahead of incomplete registry — finish DDL, keep version.
        _assert_not_in_transaction(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            _exec_ddl_statements(conn, SOURCE_REGISTRY_SCHEMA_V7)
            if not _v7_shape_complete(conn):
                raise sqlite3.DatabaseError(
                    "incomplete source registry schema at version >= 7; "
                    "refusing ambiguous repair"
                )
            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_errors:
                raise sqlite3.DatabaseError(
                    f"foreign_key_check failed after source registry repair: {fk_errors}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, SOURCE_REGISTRY_SCHEMA_V7)
        if not _v7_shape_complete(conn):
            raise sqlite3.DatabaseError(
                "source registry migration did not produce required tables/columns"
            )
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after source registry migrate: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 7)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _source_overrides_allows_web(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_overrides'"
    ).fetchone()
    if not row or not row[0]:
        return True
    return "'web'" in row[0] or '"web"' in row[0]


def _migrate_source_overrides_updated_by_web(conn: sqlite3.Connection) -> None:
    """Rebuild source_overrides so updated_by may be cli|import|web."""
    if _source_overrides_allows_web(conn):
        return
    before = conn.execute("SELECT COUNT(*) FROM source_overrides").fetchone()[0]
    conn.execute(
        """CREATE TABLE source_overrides_v8 (
            source_key TEXT PRIMARY KEY,
            enabled INTEGER CHECK (enabled IS NULL OR enabled IN (0, 1)),
            always_surface INTEGER CHECK (always_surface IS NULL OR always_surface IN (0, 1)),
            priority INTEGER CHECK (priority IS NULL OR (priority >= 0 AND priority <= 100)),
            newsletter_type TEXT,
            grouping_policy TEXT,
            summary_profile TEXT,
            expected_cadence TEXT,
            display_name TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL CHECK (updated_by IN ('cli', 'import', 'web')),
            FOREIGN KEY (source_key) REFERENCES sources(source_key) ON DELETE RESTRICT
        )"""
    )
    conn.execute(
        """INSERT INTO source_overrides_v8 (
            source_key, enabled, always_surface, priority, newsletter_type,
            grouping_policy, summary_profile, expected_cadence, display_name, notes,
            updated_at, updated_by
           )
           SELECT source_key, enabled, always_surface, priority, newsletter_type,
                  grouping_policy, summary_profile, expected_cadence, display_name, notes,
                  updated_at, updated_by
           FROM source_overrides"""
    )
    after = conn.execute("SELECT COUNT(*) FROM source_overrides_v8").fetchone()[0]
    if after != before:
        raise sqlite3.DatabaseError(
            f"source_overrides migration count mismatch: before={before} after={after}"
        )
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise sqlite3.DatabaseError(
            f"foreign_key_check failed during source_overrides migrate: {fk_errors}"
        )
    conn.execute("DROP TABLE source_overrides")
    conn.execute("ALTER TABLE source_overrides_v8 RENAME TO source_overrides")


def _seed_rating_reason_codes(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO rating_reason_codes
           (code, polarity, label, sort_order, active)
           VALUES (?, ?, ?, ?, 1)""",
        RATING_REASON_SEED,
    )


def ensure_web_schema(conn: sqlite3.Connection) -> None:
    """Atomic schema v8: web archive, ratings, interaction. Part of canonical init."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    if get_schema_version(conn) >= 8 and _source_overrides_allows_web(conn):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rollup_runs'"
        ).fetchone()
        if row is not None:
            return
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Ensure v7 tables exist before depending on sources FK for overrides rebuild.
        _exec_ddl_statements(conn, SOURCE_REGISTRY_SCHEMA_V7)
        _migrate_source_overrides_updated_by_web(conn)
        _exec_ddl_statements(conn, WEB_SCHEMA_V8)
        _seed_rating_reason_codes(conn)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after web schema migrate: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 8)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _reader_bodies_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='message_reader_bodies'"
    ).fetchone()
    return row is not None


def _validate_reader_bodies_shape(
    conn: sqlite3.Connection, *, required: frozenset[str]
) -> None:
    if not _reader_bodies_table_exists(conn):
        raise sqlite3.DatabaseError("message_reader_bodies table missing")
    cols = _table_columns(conn, "message_reader_bodies")
    missing = required - cols
    if missing:
        raise sqlite3.DatabaseError(
            f"message_reader_bodies missing columns: {sorted(missing)}"
        )


def _assert_not_in_transaction(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise sqlite3.DatabaseError("migration must not run inside caller transaction")


def ensure_message_reader_bodies_v9(conn: sqlite3.Connection) -> None:
    """Schema v9: reader body store."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    if ver >= 9 and _reader_bodies_table_exists(conn):
        _validate_reader_bodies_shape(conn, required=_V9_REQUIRED_COLUMNS)
        return
    if ver < 8:
        raise sqlite3.DatabaseError("schema v8 required before v9 migration")
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, MESSAGE_READER_BODIES_V9)
        _validate_reader_bodies_shape(conn, required=_V9_REQUIRED_COLUMNS)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after reader bodies v9: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 9)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_summaries_litellm_v11(conn: sqlite3.Connection) -> None:
    """Schema v11: rollup_runs.summaries_litellm column."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='rollup_runs'"
    ).fetchone()
    cols = _table_columns(conn, "rollup_runs") if row is not None else set()
    if ver >= 11 and "summaries_litellm" in cols:
        return
    if ver < 10:
        ensure_message_reader_bodies_v10(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rollup_runs'"
        ).fetchone()
        if exists is None:
            _exec_ddl_statements(conn, WEB_SCHEMA_V8)
        cols = _table_columns(conn, "rollup_runs")
        if "summaries_litellm" not in cols:
            conn.execute(
                "ALTER TABLE rollup_runs ADD COLUMN summaries_litellm INTEGER"
            )
        _bump_schema_version_in_txn(conn, 11)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_message_reader_bodies_v10(conn: sqlite3.Connection) -> None:
    """Schema v10: reader provenance columns."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    if ver >= 10 and _reader_bodies_table_exists(conn):
        cols = _table_columns(conn, "message_reader_bodies")
        if _V10_REQUIRED_COLUMNS.issubset(cols):
            _validate_reader_bodies_shape(conn, required=_V10_REQUIRED_COLUMNS)
            return
        # Narrow repair: version ahead of v10 columns — ALTER in place.
    elif ver < 9 or not _reader_bodies_table_exists(conn):
        ensure_message_reader_bodies_v9(conn)
        ver = get_schema_version(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not _reader_bodies_table_exists(conn):
            _exec_ddl_statements(conn, MESSAGE_READER_BODIES_V9)
        cols = _table_columns(conn, "message_reader_bodies")
        if "reader_text_version" not in cols:
            conn.execute(
                "ALTER TABLE message_reader_bodies ADD COLUMN reader_text_version "
                "INTEGER NOT NULL DEFAULT 0 CHECK (reader_text_version >= 0)"
            )
        if "source_body_length" not in cols:
            conn.execute(
                "ALTER TABLE message_reader_bodies ADD COLUMN source_body_length "
                "INTEGER NOT NULL DEFAULT -1 CHECK (source_body_length >= -1)"
            )
        if "reader_content_hash" not in cols:
            conn.execute(
                "ALTER TABLE message_reader_bodies ADD COLUMN reader_content_hash TEXT"
            )
        if "reader_hash_authoritative" not in cols:
            conn.execute(
                "ALTER TABLE message_reader_bodies ADD COLUMN reader_hash_authoritative "
                "INTEGER NOT NULL DEFAULT 0 CHECK (reader_hash_authoritative IN (0, 1))"
            )
        if "first_indexed_at" not in cols:
            conn.execute(
                "ALTER TABLE message_reader_bodies ADD COLUMN first_indexed_at TEXT"
            )
        conn.execute(
            """UPDATE message_reader_bodies
               SET first_indexed_at = COALESCE(first_indexed_at, updated_at),
                   reader_text_version = COALESCE(reader_text_version, 0),
                   source_body_length = COALESCE(source_body_length, -1),
                   reader_hash_authoritative = COALESCE(reader_hash_authoritative, 0)
               WHERE first_indexed_at IS NULL OR reader_text_version IS NULL"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reader_bodies_version "
            "ON message_reader_bodies(reader_text_version)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reader_bodies_last_seen "
            "ON message_reader_bodies(last_seen_at)"
        )
        _validate_reader_bodies_shape(conn, required=_V10_REQUIRED_COLUMNS)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after reader bodies v10: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 10)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_webpage_queue_v12(conn: sqlite3.Connection) -> None:
    """Schema v12: webpage reading queue."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    if ver >= 12 and "webpage_queue" in _existing_tables(conn):
        return
    if ver < 11:
        ensure_summaries_litellm_v11(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, WEBPAGE_QUEUE_V12)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after webpage_queue v12: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 12)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_webpage_queue_v13(conn: sqlite3.Connection) -> None:
    """Schema v13: persist fetched webpage bodies for lookback re-inclusion."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    cols = (
        _table_columns(conn, "webpage_queue")
        if "webpage_queue" in _existing_tables(conn)
        else set()
    )
    if ver >= 13 and _V13_WEBPAGE_COLUMN_NAMES.issubset(cols):
        return
    if ver < 12:
        ensure_webpage_queue_v12(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = _table_columns(conn, "webpage_queue")
        for col, typ in _V13_WEBPAGE_COLUMNS:
            if col not in existing:
                conn.execute(f"ALTER TABLE webpage_queue ADD COLUMN {col} {typ}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webpage_queue_created "
            "ON webpage_queue(created_at)"
        )
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after webpage_queue v13: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 13)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_reddit_catalog_v14(conn: sqlite3.Connection) -> None:
    """Schema v14: Reddit subscription catalog for the web picker."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    if ver >= 14 and "reddit_sub_catalog" in _existing_tables(conn):
        return
    if ver < 13:
        ensure_webpage_queue_v13(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, REDDIT_CATALOG_V14)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after reddit_sub_catalog v14: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 14)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_source_fetch_cache_v15(conn: sqlite3.Connection) -> None:
    """Schema v15: persisted Reddit/LinkedIn listing and article-body caches."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    required = {
        "reddit_posts",
        "reddit_listing_snapshots",
        "linkedin_posts",
        "linkedin_listing_snapshots",
        "linkedin_article_bodies",
    }
    if ver >= 15 and required.issubset(_existing_tables(conn)):
        return
    if ver < 14:
        ensure_reddit_catalog_v14(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, SOURCE_FETCH_CACHE_V15)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after source fetch cache v15: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 15)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_scholar_paper_bodies_v16(conn: sqlite3.Connection) -> None:
    """Schema v16: cached Scholar paper landing-page bodies."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    if ver >= 16 and "scholar_paper_bodies" in _existing_tables(conn):
        return
    if ver < 15:
        ensure_source_fetch_cache_v15(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, SCHOLAR_PAPER_BODIES_V16)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after scholar_paper_bodies v16: {fk_errors}"
            )
        _bump_schema_version_in_txn(conn, 16)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_message_corpus_fts_v17(conn: sqlite3.Connection) -> None:
    """Schema v17: FTS5 corpus over reader bodies, titles, and summaries."""
    apply_connection_pragmas(conn)
    refuse_unsupported_schema_version(conn)
    ver = get_schema_version(conn)
    if ver >= 17 and "message_corpus_fts" in _existing_tables(conn):
        return
    if ver < 16:
        ensure_scholar_paper_bodies_v16(conn)
    _assert_not_in_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _exec_ddl_statements(conn, MESSAGE_CORPUS_FTS_V17)
        except sqlite3.OperationalError as exc:
            raise sqlite3.DatabaseError(
                "SQLite FTS5 is required for schema v17 (message_corpus_fts); "
                f"this build cannot create the corpus index: {exc}"
            ) from exc
        from rollup.corpus import rebuild_corpus

        rebuild_corpus(conn)
        _bump_schema_version_in_txn(conn, 17)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def run_schema_migrations(conn: sqlite3.Connection) -> None:
    """Authoritative ordered migration steps after MVP bootstrap."""
    refuse_unsupported_schema_version(conn)
    ensure_canonical_cache_tables(conn)
    ensure_source_registry_schema(conn)
    ensure_web_schema(conn)
    ensure_message_reader_bodies_v9(conn)
    ensure_message_reader_bodies_v10(conn)
    ensure_summaries_litellm_v11(conn)
    ensure_webpage_queue_v12(conn)
    ensure_webpage_queue_v13(conn)
    ensure_reddit_catalog_v14(conn)
    ensure_source_fetch_cache_v15(conn)
    ensure_scholar_paper_bodies_v16(conn)
    ensure_message_corpus_fts_v17(conn)
    validate_canonical_schema(conn)


