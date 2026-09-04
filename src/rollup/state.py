"""SQLite state for seen undated messages and summary caches."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from rollup.cache_keys import canonicalize_provider_options

SCHEMA_VERSION = 16

BUSY_TIMEOUT_MS = 5000

WEB_SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS rollup_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'partial')),
    mode TEXT CHECK (mode IN ('manual', 'cron') OR mode IS NULL),
    rollup_version TEXT,
    manifest_schema_version INTEGER,
    report_schema_version INTEGER,
    entry_index_version INTEGER NOT NULL DEFAULT 0,
    stats_completeness TEXT NOT NULL
        CHECK (stats_completeness IN ('full', 'manifest_partial')),
    window_start TEXT,
    window_end TEXT,
    lookback_days INTEGER,
    digest_fingerprint TEXT,
    messages_included INTEGER,
    messages_skipped_outside_window INTEGER,
    messages_skipped_seen_undated INTEGER,
    messages_deduped INTEGER,
    messages_skipped_disabled_source INTEGER,
    groups_created INTEGER,
    sources_included INTEGER,
    summaries_ollama INTEGER,
    summaries_litellm INTEGER,
    summaries_cache INTEGER,
    summaries_fallback INTEGER,
    summaries_errors INTEGER,
    summaries_final_review_applied INTEGER,
    group_summaries_succeeded INTEGER,
    warning_count INTEGER,
    index_warning_count INTEGER NOT NULL DEFAULT 0,
    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
    manifest_relpath TEXT,
    markdown_relpath TEXT,
    html_relpath TEXT,
    index_source TEXT NOT NULL
        CHECK (index_source IN ('pipeline', 'manifest_backfill')),
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rollup_runs_started
    ON rollup_runs(started_at DESC, run_id DESC);
CREATE TABLE IF NOT EXISTS rollup_entries (
    run_id TEXT NOT NULL,
    message_key TEXT NOT NULL,
    source_key_observed TEXT,
    group_id TEXT,
    group_type TEXT,
    group_display_name TEXT,
    section_key TEXT,
    section_position INTEGER NOT NULL CHECK (section_position >= 0),
    group_position INTEGER,
    entry_position INTEGER NOT NULL CHECK (entry_position >= 0),
    display_position INTEGER NOT NULL CHECK (display_position >= 0),
    folder_name TEXT,
    subject TEXT,
    sender TEXT,
    date_parsed TEXT,
    date_raw TEXT,
    newsletter_type TEXT,
    summary TEXT,
    summary_source TEXT,
    primary_link TEXT,
    links_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, message_key),
    UNIQUE (run_id, display_position),
    FOREIGN KEY (run_id) REFERENCES rollup_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rollup_entries_source_run
    ON rollup_entries(source_key_observed, run_id);
CREATE INDEX IF NOT EXISTS idx_rollup_entries_message
    ON rollup_entries(message_key);
CREATE TABLE IF NOT EXISTS message_source_links (
    message_key TEXT PRIMARY KEY,
    source_key_observed TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_source_links_source
    ON message_source_links(source_key_observed);
CREATE TABLE IF NOT EXISTS message_interaction (
    message_key TEXT PRIMARY KEY,
    read_at TEXT,
    saved_at TEXT,
    dismissed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_interaction_dismissed
    ON message_interaction(dismissed_at);
CREATE INDEX IF NOT EXISTS idx_message_interaction_saved
    ON message_interaction(saved_at);
CREATE TABLE IF NOT EXISTS message_ratings (
    message_key TEXT PRIMARY KEY,
    stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_ratings_updated
    ON message_ratings(updated_at);
CREATE TABLE IF NOT EXISTS rating_reason_codes (
    code TEXT PRIMARY KEY,
    polarity TEXT NOT NULL CHECK (polarity IN ('positive', 'negative')),
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE TABLE IF NOT EXISTS message_rating_reasons (
    message_key TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (message_key, reason_code),
    FOREIGN KEY (message_key) REFERENCES message_ratings(message_key)
        ON DELETE CASCADE,
    FOREIGN KEY (reason_code) REFERENCES rating_reason_codes(code)
        ON DELETE RESTRICT
);
"""

MESSAGE_READER_BODIES_V9 = """
CREATE TABLE IF NOT EXISTS message_reader_bodies (
    message_key TEXT PRIMARY KEY CHECK (length(message_key) > 0),
    content_hash TEXT NOT NULL CHECK (
        length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'
    ),
    stored_body_hash TEXT NOT NULL CHECK (
        length(stored_body_hash) = 64 AND stored_body_hash NOT GLOB '*[^0-9a-f]*'
    ),
    body_text TEXT NOT NULL,
    truncated INTEGER NOT NULL CHECK (truncated IN (0, 1)),
    updated_at TEXT NOT NULL CHECK (length(updated_at) > 0),
    last_seen_at TEXT NOT NULL CHECK (length(last_seen_at) > 0)
);
"""

_V9_REQUIRED_COLUMNS = frozenset(
    {
        "message_key",
        "content_hash",
        "stored_body_hash",
        "body_text",
        "truncated",
        "updated_at",
        "last_seen_at",
    }
)

_V10_REQUIRED_COLUMNS = _V9_REQUIRED_COLUMNS | frozenset(
    {
        "reader_text_version",
        "source_body_length",
        "reader_content_hash",
        "reader_hash_authoritative",
        "first_indexed_at",
    }
)

RATING_REASON_SEED = (
    ("not_relevant", "negative", "Not relevant", 10),
    ("too_repetitive", "negative", "Too repetitive", 20),
    ("too_long", "negative", "Too long", 30),
    ("too_promotional", "negative", "Too promotional", 40),
    ("weak_summary", "negative", "Weak summary", 50),
    ("poor_links", "negative", "Poor links", 60),
    ("great_analysis", "positive", "Great analysis", 110),
    ("useful_professionally", "positive", "Useful professionally", 120),
    ("great_links", "positive", "Great links", 130),
    ("concise", "positive", "Concise", 140),
    ("original_perspective", "positive", "Original perspective", 150),
    ("worth_saving", "positive", "Worth saving", 160),
)

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

SOURCE_REGISTRY_SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS sources (
    source_key TEXT PRIMARY KEY,
    identity_version INTEGER NOT NULL DEFAULT 1,
    lifecycle TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle IN ('active', 'superseded')),
    superseded_by TEXT,
    display_name_observed TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (superseded_by) REFERENCES sources(source_key) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS source_observations (
    source_key TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    message_count_total INTEGER NOT NULL DEFAULT 0
        CHECK (message_count_total >= 0),
    observed_from_addrs_json TEXT NOT NULL,
    observed_list_id TEXT,
    last_folder_name TEXT,
    last_detected_newsletter_type TEXT,
    cadence_label TEXT NOT NULL
        CHECK (cadence_label IN (
            'unknown', 'realtime', 'daily', 'several_per_week', 'weekly', 'irregular'
        )),
    cadence_confidence REAL NOT NULL
        CHECK (cadence_confidence >= 0 AND cadence_confidence <= 1),
    cadence_sample_count INTEGER NOT NULL DEFAULT 0
        CHECK (cadence_sample_count >= 0),
    cadence_median_hours REAL,
    cadence_calculated_at TEXT,
    last_subject_family TEXT,
    FOREIGN KEY (source_key) REFERENCES sources(source_key) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS source_overrides (
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
    updated_by TEXT NOT NULL CHECK (updated_by IN ('cli', 'import')),
    FOREIGN KEY (source_key) REFERENCES sources(source_key) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS source_aliases (
    alias_key TEXT PRIMARY KEY,
    canonical_source_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT,
    CHECK (alias_key != canonical_source_key),
    FOREIGN KEY (canonical_source_key) REFERENCES sources(source_key) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS source_observation_dedup (
    source_key TEXT NOT NULL,
    message_key TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    PRIMARY KEY (source_key, message_key),
    FOREIGN KEY (source_key) REFERENCES sources(source_key) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS source_cadence_samples (
    source_key TEXT NOT NULL,
    message_key TEXT NOT NULL,
    date_parsed TEXT NOT NULL,
    PRIMARY KEY (source_key, message_key),
    FOREIGN KEY (source_key) REFERENCES sources(source_key) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_source_observations_last_seen
    ON source_observations(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_source_aliases_canonical
    ON source_aliases(canonical_source_key);
CREATE INDEX IF NOT EXISTS idx_source_cadence_samples_dated
    ON source_cadence_samples(source_key, date_parsed);
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

GROUP_SUMMARY_SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS group_summary_generations (
    generation_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    member_fingerprint TEXT NOT NULL,
    grouping_version TEXT NOT NULL,
    group_type TEXT NOT NULL,
    variant_key TEXT NOT NULL DEFAULT 'default',
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
    output_fingerprint TEXT NOT NULL,
    usability_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    UNIQUE (
        group_id, member_fingerprint, grouping_version, group_type, variant_key,
        provider, profile_name, model, prompt_style, prompt_version,
        temperature, num_ctx, options_json, summary_input_hash
    )
);
CREATE INDEX IF NOT EXISTS idx_group_summary_lookup
    ON group_summary_generations (group_id, member_fingerprint, summary_input_hash);
"""

# Simple cache-key lookup table used by group_summarize.py (cache_key = sha256).
GROUP_SUMMARY_BY_KEY_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_summary_by_key (
    cache_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);
"""

FINAL_REVIEW_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS final_review_generations (
    digest_fingerprint TEXT NOT NULL,
    review_input_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    temperature REAL NOT NULL,
    num_ctx INTEGER,
    options_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        digest_fingerprint,
        review_input_hash,
        provider,
        profile_name,
        model,
        prompt_version,
        temperature,
        num_ctx,
        options_json
    )
);
"""

WEBPAGE_QUEUE_V12 = """
CREATE TABLE IF NOT EXISTS webpage_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    display_title TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'failed', 'ingested')),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    ingested_at TEXT,
    ingested_message_key TEXT,
    ingested_run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_webpage_queue_status_created
    ON webpage_queue(status, created_at);
"""

REDDIT_CATALOG_V14 = """
CREATE TABLE IF NOT EXISTS reddit_sub_catalog (
    name TEXT PRIMARY KEY,
    title TEXT,
    over_18 INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reddit_sub_catalog_fetched
    ON reddit_sub_catalog(fetched_at);
"""

SOURCE_FETCH_CACHE_V15 = """
CREATE TABLE IF NOT EXISTS reddit_posts (
    post_id TEXT PRIMARY KEY,
    subreddit TEXT NOT NULL,
    title TEXT NOT NULL,
    selftext TEXT NOT NULL,
    author TEXT NOT NULL,
    permalink TEXT NOT NULL,
    url TEXT NOT NULL,
    score INTEGER NOT NULL,
    num_comments INTEGER NOT NULL,
    created_at TEXT,
    over_18 INTEGER NOT NULL DEFAULT 0,
    is_self INTEGER NOT NULL DEFAULT 1,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reddit_posts_subreddit
    ON reddit_posts(subreddit);
CREATE TABLE IF NOT EXISTS reddit_listing_snapshots (
    subreddit TEXT NOT NULL,
    sort TEXT NOT NULL,
    time_filter TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    post_ids_json TEXT NOT NULL,
    PRIMARY KEY (subreddit, sort, time_filter)
);
CREATE INDEX IF NOT EXISTS idx_reddit_listing_snapshots_fetched
    ON reddit_listing_snapshots(fetched_at);
CREATE TABLE IF NOT EXISTS linkedin_posts (
    message_key TEXT PRIMARY KEY,
    activity_id TEXT,
    author_name TEXT NOT NULL,
    author_member_id TEXT,
    text TEXT NOT NULL,
    permalink TEXT NOT NULL,
    created_at TEXT,
    article_url TEXT,
    article_title TEXT,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS linkedin_listing_snapshots (
    slug TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    post_keys_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_linkedin_listing_snapshots_fetched
    ON linkedin_listing_snapshots(fetched_at);
CREATE TABLE IF NOT EXISTS linkedin_article_bodies (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    body_text TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

SCHOLAR_PAPER_BODIES_V16 = """
CREATE TABLE IF NOT EXISTS scholar_paper_bodies (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    body_text TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scholar_paper_bodies_fetched
    ON scholar_paper_bodies(fetched_at);
"""

_V13_WEBPAGE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("fetched_title", "TEXT"),
    ("body_text", "TEXT"),
    ("content_hash", "TEXT"),
    ("fetched_at", "TEXT"),
)
_V13_WEBPAGE_COLUMN_NAMES = frozenset(name for name, _typ in _V13_WEBPAGE_COLUMNS)


_SUMMARIES_COMPOSITE_PK = (
    "primary key (message_key, content_hash, newsletter_type, model)"
)
_SUMMARY_GENERATIONS_INPUT_HASH_PK = "summary_input_hash"

# Canonical full shape at SCHEMA_VERSION (core + empty cache/feature tables).
CANONICAL_TABLES: frozenset[str] = frozenset(
    {
        "schema_version",
        "seen_messages",
        "summaries",
        "summary_generations",
        "final_review_generations",
        "group_summary_generations",
        "group_summary_by_key",
        "sources",
        "source_observations",
        "source_overrides",
        "source_aliases",
        "source_observation_dedup",
        "source_cadence_samples",
        "rollup_runs",
        "rollup_entries",
        "message_source_links",
        "message_interaction",
        "message_ratings",
        "rating_reason_codes",
        "message_rating_reasons",
        "message_reader_bodies",
        "webpage_queue",
        "reddit_sub_catalog",
        "reddit_posts",
        "reddit_listing_snapshots",
        "linkedin_posts",
        "linkedin_listing_snapshots",
        "linkedin_article_bodies",
        "scholar_paper_bodies",
    }
)

_V7_REQUIRED_TABLES: frozenset[str] = frozenset(
    {
        "sources",
        "source_observations",
        "source_overrides",
        "source_aliases",
        "source_observation_dedup",
        "source_cadence_samples",
    }
)


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
    validate_canonical_schema(conn)


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
