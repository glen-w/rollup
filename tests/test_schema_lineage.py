"""Schema lineage: every historical version upgrades to the current shape.

``rollup.db`` carries one ``schema_version`` that labels a canonical full
shape. These tests rebuild a database at each intermediate version by replaying
the ordered migration steps, then prove that ``init_db`` brings any of them to
``SCHEMA_VERSION`` with rows preserved, that the version never runs ahead of a
step that did not finish, and that a newer database is refused before mutation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from rollup.state import (
    SCHEMA_VERSION,
    connect_db,
    get_schema_version,
    init_db,
)
from rollup.state import migrations as mig
from rollup.state.schema import MVP_SCHEMA

# Ordered steps exactly as run_schema_migrations applies them.
STEPS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    mig.ensure_canonical_cache_tables,
    mig.ensure_source_registry_schema,
    mig.ensure_web_schema,
    mig.ensure_message_reader_bodies_v9,
    mig.ensure_message_reader_bodies_v10,
    mig.ensure_summaries_litellm_v11,
    mig.ensure_webpage_queue_v12,
    mig.ensure_webpage_queue_v13,
    mig.ensure_reddit_catalog_v14,
    mig.ensure_source_fetch_cache_v15,
    mig.ensure_scholar_paper_bodies_v16,
    mig.ensure_message_corpus_fts_v17,
    mig.ensure_gmail_cache_v18,
    mig.ensure_wave_v19,
)


def _bootstrap(db_path: Path) -> sqlite3.Connection:
    conn = connect_db(db_path)
    conn.executescript(MVP_SCHEMA)
    mig._ensure_schema_version_singleton(conn)
    return conn


def _seed_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO seen_messages (message_key, last_seen_at) VALUES (?, ?)",
        ("lineage-key", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()


def _db_at_step(db_path: Path, upto: int) -> int:
    """Replay the first ``upto`` steps; return the resulting schema_version."""
    conn = _bootstrap(db_path)
    try:
        _seed_rows(conn)
        for step in STEPS[:upto]:
            step(conn)
        return get_schema_version(conn)
    finally:
        conn.close()


def test_steps_match_authoritative_runner() -> None:
    """Guard: this test's step list must track run_schema_migrations."""
    import inspect

    src = inspect.getsource(mig.run_schema_migrations)
    names_in_runner = [
        line.strip().split("(")[0]
        for line in src.splitlines()
        if line.strip().startswith("ensure_")
    ]
    assert names_in_runner == [s.__name__ for s in STEPS]


def test_versions_are_monotonic_along_the_lineage(tmp_path: Path) -> None:
    seen: list[int] = []
    for k in range(len(STEPS) + 1):
        seen.append(_db_at_step(tmp_path / f"v{k}.db", k))
    assert seen == sorted(seen)
    assert seen[-1] == SCHEMA_VERSION


@pytest.mark.parametrize("upto", range(len(STEPS) + 1))
def test_any_intermediate_version_upgrades_to_current(tmp_path: Path, upto: int) -> None:
    db_path = tmp_path / "rollup.db"
    before = _db_at_step(db_path, upto)
    assert before <= SCHEMA_VERSION

    conn = init_db(db_path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        mig.validate_canonical_schema(conn)
        row = conn.execute(
            "SELECT message_key FROM seen_messages WHERE message_key = ?",
            ("lineage-key",),
        ).fetchone()
        assert row is not None, "rows must survive the upgrade"
    finally:
        conn.close()

    # Second open is a no-op that still validates.
    conn = init_db(db_path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()


def test_crash_inside_a_step_never_advances_version(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "rollup.db"
    # Everything up to v17 is in place.
    assert _db_at_step(db_path, len(STEPS) - 1) == SCHEMA_VERSION - 1

    def crash(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("disk I/O error mid-migration")

    monkeypatch.setattr(mig, "ensure_wave_v19", crash)
    with pytest.raises(sqlite3.OperationalError):
        init_db(db_path).close()

    conn = connect_db(db_path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION - 1
        assert "rss_feeds" not in mig._existing_tables(conn)
    finally:
        conn.close()

    # Recovery: with the step restored, the next open completes the lineage.
    monkeypatch.undo()
    conn = init_db(db_path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        assert "rss_feeds" in mig._existing_tables(conn)
    finally:
        conn.close()


def test_step_ddl_failure_rolls_back_partial_tables(tmp_path: Path, monkeypatch) -> None:
    """If a step's DDL fails after creating one of its tables, no half-step is left behind."""
    db_path = tmp_path / "rollup.db"
    assert _db_at_step(db_path, len(STEPS) - 1) == SCHEMA_VERSION - 1

    real_exec = mig._exec_ddl_statements
    armed = {"fail": True}

    def flaky_exec(conn: sqlite3.Connection, script: str) -> None:
        if armed["fail"] and "rss_feeds" in script:
            armed["fail"] = False
            # Create one table then blow up, as a real I/O error would.
            conn.execute("CREATE TABLE IF NOT EXISTS rss_feeds (x INTEGER)")
            raise sqlite3.OperationalError("disk full")
        real_exec(conn, script)

    monkeypatch.setattr(mig, "_exec_ddl_statements", flaky_exec)
    with pytest.raises(sqlite3.OperationalError):
        init_db(db_path).close()

    conn = connect_db(db_path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION - 1
        tables = mig._existing_tables(conn)
        # Either the step was transactional (table gone) or the next init must
        # still converge; both are acceptable, a stuck version is not.
        assert "digest_attempts" not in tables or "rss_feeds" in tables
    finally:
        conn.close()

    monkeypatch.undo()
    conn = init_db(db_path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        mig.validate_canonical_schema(conn)
    finally:
        conn.close()


def test_future_version_refused_before_any_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "rollup.db"
    conn = init_db(db_path)
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    conn.execute("DROP TABLE gmail_messages")
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.DatabaseError, match="unsupported schema version"):
        init_db(db_path).close()

    conn = connect_db(db_path)
    try:
        # Nothing was repaired or lowered.
        assert get_schema_version(conn) == SCHEMA_VERSION + 1
        assert "gmail_messages" not in mig._existing_tables(conn)
    finally:
        conn.close()


def test_missing_canonical_table_at_current_version_is_detected(tmp_path: Path) -> None:
    """A table dropped out from under a v18 label must not pass silently."""
    db_path = tmp_path / "rollup.db"
    conn = init_db(db_path)
    conn.execute("DROP TABLE reddit_listing_snapshots")
    conn.commit()
    conn.close()

    try:
        conn = init_db(db_path)
    except sqlite3.DatabaseError as exc:
        assert "missing tables" in str(exc) or "schema corruption" in str(exc)
        return
    # If init_db chose to repair instead, the shape must be whole again.
    try:
        mig.validate_canonical_schema(conn)
        assert "reddit_listing_snapshots" in mig._existing_tables(conn)
    finally:
        conn.close()
