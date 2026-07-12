"""Schema v9/v10 reader body migration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.state import (
    MESSAGE_READER_BODIES_V9,
    SCHEMA_VERSION,
    connect_db,
    ensure_message_reader_bodies_v10,
    ensure_message_reader_bodies_v9,
    ensure_web_schema,
    get_schema_version,
    init_db,
)


def test_fresh_db_schema_version(tmp_path: Path):
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION == 10
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='message_reader_bodies'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_v8_to_v9_migration(tmp_path: Path):
    db = tmp_path / "rollup.db"
    conn = connect_db(db)
    conn.executescript(
        """
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 8);
        """
    )
    conn.commit()
    ensure_message_reader_bodies_v9(conn)
    assert get_schema_version(conn) == 9
    ensure_message_reader_bodies_v10(conn)
    assert get_schema_version(conn) == 10
    conn.close()


def test_repair_schema_version_without_reader_bodies_table(tmp_path: Path):
    """Stale or partial state: version bumped but table missing."""
    db = tmp_path / "rollup.db"
    conn = connect_db(db)
    conn.executescript(
        """
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 10);
        """
    )
    conn.commit()
    conn.close()
    conn = init_db(db)
    assert get_schema_version(conn) == 10
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='message_reader_bodies'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_v9_migration_rollback_hook(tmp_path: Path, monkeypatch):
    db = tmp_path / "rollup.db"
    conn = connect_db(db)
    conn.executescript(
        """
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 8);
        """
    )
    conn.commit()

    import rollup.state as st

    original = st._exec_ddl_statements
    calls = {"n": 0}

    def flaky(conn, script):
        calls["n"] += 1
        original(conn, script)
        if calls["n"] >= 1:
            raise RuntimeError("injected failure")

    monkeypatch.setattr(st, "_exec_ddl_statements", flaky)
    with pytest.raises(RuntimeError):
        ensure_message_reader_bodies_v9(conn)
    conn2 = connect_db(db)
    assert get_schema_version(conn2) == 8
    row = conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='message_reader_bodies'"
    ).fetchone()
    assert row is None
    conn2.close()
    conn.close()
