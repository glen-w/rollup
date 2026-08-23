"""Authoritative schema migration integrity tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlite3

from rollup.state import (
    CANONICAL_TABLES,
    SCHEMA_VERSION,
    connect_db,
    get_schema_version,
    init_db,
    init_db_with_summaries,
    refuse_unsupported_schema_version,
    validate_canonical_schema,
)


def test_fresh_db_canonical_full_shape(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION
    validate_canonical_schema(conn)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert CANONICAL_TABLES.issubset(tables)
    conn.close()


def test_init_db_and_with_summaries_same_shape(tmp_path: Path) -> None:
    a = init_db(tmp_path / "a.db")
    b = init_db_with_summaries(tmp_path / "b.db")
    ta = {
        r[0]
        for r in a.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    tb = {
        r[0]
        for r in b.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert get_schema_version(a) == get_schema_version(b) == SCHEMA_VERSION
    assert CANONICAL_TABLES.issubset(ta)
    assert CANONICAL_TABLES.issubset(tb)
    a.close()
    b.close()


def test_idempotent_repeated_init(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    conn.execute(
        "INSERT INTO seen_messages (message_key, last_seen_at) VALUES ('k1', 't')"
    )
    conn.commit()
    conn.close()
    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION
    row = conn.execute(
        "SELECT message_key FROM seen_messages WHERE message_key = 'k1'"
    ).fetchone()
    assert row is not None
    validate_canonical_schema(conn)
    conn.close()


def test_refuse_future_version_no_mutate(tmp_path: Path) -> None:
    future = SCHEMA_VERSION + 1
    db = tmp_path / "rollup.db"
    conn = connect_db(db)
    conn.executescript(
        f"""
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, {future});
        CREATE TABLE seen_messages (
            message_key TEXT PRIMARY KEY, last_seen_at TEXT NOT NULL
        );
        INSERT INTO seen_messages (message_key, last_seen_at) VALUES ('keep', 't');
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(
        sqlite3.DatabaseError, match=f"unsupported schema version {future}"
    ):
        init_db(db)

    conn = connect_db(db)
    assert get_schema_version(conn) == future
    row = conn.execute(
        "SELECT message_key FROM seen_messages WHERE message_key = 'keep'"
    ).fetchone()
    assert row is not None
    # Must not have created canonical tables via a partial migrate.
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert "rollup_runs" not in tables
    conn.close()


def test_reproduced_future_to_current_downgrade_refused(tmp_path: Path) -> None:
    """Former bug: singleton repair wrote SCHEMA_VERSION and lowered a future DB."""
    future = SCHEMA_VERSION + 1
    db = tmp_path / "rollup.db"
    conn = connect_db(db)
    conn.executescript(
        f"""
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, {future});
        """
    )
    conn.commit()
    with pytest.raises(
        sqlite3.DatabaseError, match=f"unsupported schema version {future}"
    ):
        refuse_unsupported_schema_version(conn)
    assert get_schema_version(conn) == future
    with pytest.raises(
        sqlite3.DatabaseError, match=f"unsupported schema version {future}"
    ):
        init_db(db)
    conn2 = connect_db(db)
    assert get_schema_version(conn2) == future
    conn2.close()
    conn.close()


def test_incomplete_v7_repaired_or_refused(tmp_path: Path) -> None:
    """Version >= 7 with only sources must not be accepted as complete without repair."""
    db = tmp_path / "rollup.db"
    conn = connect_db(db)
    conn.executescript(
        """
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, 7);
        CREATE TABLE sources (
            source_key TEXT PRIMARY KEY,
            identity_version INTEGER NOT NULL DEFAULT 1,
            lifecycle TEXT NOT NULL DEFAULT 'active',
            superseded_by TEXT,
            display_name_observed TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION
    validate_canonical_schema(conn)
    for name in (
        "source_observations",
        "source_overrides",
        "source_aliases",
        "source_observation_dedup",
        "source_cadence_samples",
    ):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        assert row is not None, name
    conn.close()


def test_preserve_rows_across_init(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    conn.execute(
        "INSERT INTO seen_messages (message_key, last_seen_at) VALUES ('m1', '2026-01-01')"
    )
    conn.execute(
        """INSERT INTO summaries
           (message_key, content_hash, newsletter_type, model, summary, created_at)
           VALUES ('m1', 'h', 'news', 'model', 's', 't')"""
    )
    conn.commit()
    conn.close()
    conn = init_db(db)
    assert (
        conn.execute("SELECT COUNT(*) FROM seen_messages").fetchone()[0] == 1
    )
    assert conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 1
    conn.close()


def test_v8_migrate_preserves_version_monotonic(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = connect_db(db)
    conn.executescript(
        """
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, 8);
        """
    )
    conn.commit()
    conn.close()
    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION
    validate_canonical_schema(conn)
    conn.close()
