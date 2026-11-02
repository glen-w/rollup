"""SQLite schema constants and DDL for rollup.db."""

from __future__ import annotations

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


