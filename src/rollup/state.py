"""SQLite state for seen undated messages and summary caches."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from rollup.cache_keys import canonicalize_provider_options

SCHEMA_VERSION = 8

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


def ensure_final_review_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(FINAL_REVIEW_SCHEMA_V5)
    _bump_schema_version_at_least(conn, 5)


def ensure_group_summary_schema(conn: sqlite3.Connection) -> None:
    """Additive schema v6: group summary caches. Preserves all prior tables."""
    conn.executescript(GROUP_SUMMARY_SCHEMA_V6)
    conn.executescript(GROUP_SUMMARY_BY_KEY_SCHEMA)
    _bump_schema_version_at_least(conn, 6)


def _bump_schema_version_at_least(conn: sqlite3.Connection, version: int) -> None:
    _migrate_schema_version_singleton(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, ?)",
        (version,),
    )
    conn.execute(
        "UPDATE schema_version SET version = ? WHERE id = 1 AND version < ?",
        (version, version),
    )
    conn.commit()


def _exec_ddl_statements(conn: sqlite3.Connection, script: str) -> None:
    """Execute DDL without executescript's implicit commit (safe inside a txn)."""
    for stmt in script.split(";"):
        text = stmt.strip()
        if text:
            conn.execute(text)


def ensure_source_registry_schema(conn: sqlite3.Connection) -> None:
    """Atomic schema v7: source registry tables. Rollback leaves valid prior DB."""
    apply_connection_pragmas(conn)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is not None and get_schema_version(conn) >= 7:
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        _exec_ddl_statements(conn, SOURCE_REGISTRY_SCHEMA_V7)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.DatabaseError(
                f"foreign_key_check failed after source registry migrate: {fk_errors}"
            )
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 7)"
        )
        conn.execute("UPDATE schema_version SET version = 7 WHERE id = 1")
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
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 8)"
        )
        conn.execute("UPDATE schema_version SET version = 8 WHERE id = 1")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def apply_connection_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    apply_connection_pragmas(conn)
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = connect_db(db_path)
    conn.executescript(MVP_SCHEMA)
    _migrate_schema_version_singleton(conn)
    _bump_schema_version_at_least(conn, 1)
    ensure_source_registry_schema(conn)
    ensure_web_schema(conn)
    return conn


def init_db_with_summaries(db_path: Path) -> sqlite3.Connection:
    conn = connect_db(db_path)
    conn.executescript(MVP_SCHEMA)
    _migrate_schema_version_singleton(conn)
    conn.executescript(SUMMARIES_SCHEMA_V2)
    conn.executescript(SUMMARIES_SCHEMA_V3)
    _migrate_summaries_schema(conn)
    _migrate_summary_generations_v4(conn)
    conn.executescript(SUMMARIES_SCHEMA_V4)
    ensure_final_review_schema(conn)
    ensure_group_summary_schema(conn)
    ensure_source_registry_schema(conn)
    ensure_web_schema(conn)
    return conn


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
        conn.execute(
            "UPDATE group_summary_by_key SET last_used_at = ? WHERE cache_key = ?",
            (datetime.now().astimezone().isoformat(), cache_key),
        )
        conn.commit()
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
