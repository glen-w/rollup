"""Offline tests for reader body stats and integrity checks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.parse import compute_content_hash
from rollup.payload_limits import MAX_READER_BODY_LEN
from rollup.reader_bodies import make_reader_body_write
from rollup.reader_body_admin import collect_stats, require_schema, run_check
from rollup.reader_body_store import upsert_reader_bodies_v2
from rollup.state import SCHEMA_VERSION, init_db
from rollup.utc import format_utc, now_utc


def _seed_entry(conn, message_key: str, run_id: str = "550e8400-e29b-41d4-a716-446655440000") -> None:
    now = format_utc(now_utc())
    conn.execute(
        """INSERT OR IGNORE INTO rollup_runs (
            run_id, started_at, status, entry_index_version, stats_completeness,
            index_source, indexed_at
           ) VALUES (?, ?, 'success', 1, 'full', 'pipeline', ?)""",
        (run_id, now, now),
    )
    conn.execute(
        """INSERT OR IGNORE INTO rollup_entries (
            run_id, message_key, source_key_observed, section_position,
            entry_position, display_position, links_json
           ) VALUES (?, ?, 'from:a@ex.com', 0, 0, 0, '[]')""",
        (run_id, message_key),
    )


def test_collect_stats_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "rollup.db"
    conn = init_db(db_path)
    stats = collect_stats(conn, db_path=db_path)
    assert stats.total_rows == 0
    assert stats.retained_entries == 0
    assert stats.coverage_pct is None
    assert stats.orphans == 0
    report = run_check(conn)
    assert report.issues == ()
    require_schema(conn)
    conn.close()


def test_collect_stats_coverage_and_orphan(tmp_path: Path) -> None:
    db_path = tmp_path / "rollup.db"
    conn = init_db(db_path)
    key = "mid:kept@ex.com"
    _seed_entry(conn, key)
    w = make_reader_body_write(key, compute_content_hash("hello"), "hello")
    upsert_reader_bodies_v2(conn, [w], seen_at=format_utc(now_utc()))
    # Orphan body with no rollup_entries row.
    orphan = make_reader_body_write(
        "mid:orphan@ex.com", compute_content_hash("x"), "orphan body"
    )
    upsert_reader_bodies_v2(conn, [orphan], seen_at=format_utc(now_utc()))
    conn.commit()

    stats = collect_stats(conn, db_path=db_path)
    assert stats.total_rows == 2
    assert stats.retained_entries == 1
    assert stats.entries_with_body == 1
    assert stats.orphans == 1
    assert stats.coverage_pct == 100.0

    report = run_check(conn)
    codes = {i.code: i.count for i in report.issues}
    assert codes.get("orphan") == 1
    conn.close()


def test_run_check_over_cap_and_bad_truncation(tmp_path: Path) -> None:
    db_path = tmp_path / "rollup.db"
    conn = init_db(db_path)
    key = "mid:cap@ex.com"
    _seed_entry(conn, key)
    now = format_utc(datetime(2026, 1, 1, tzinfo=timezone.utc))
    over = "x" * (MAX_READER_BODY_LEN + 10)
    ch = "a" * 64
    sh = "b" * 64
    conn.execute(
        """INSERT INTO message_reader_bodies (
            message_key, content_hash, stored_body_hash, body_text, truncated,
            updated_at, last_seen_at, reader_text_version, source_body_length,
            reader_hash_authoritative
           ) VALUES (?, ?, ?, ?, 0, ?, ?, 2, ?, 0)""",
        (key, ch, sh, over, now, now, len(over)),
    )
    conn.execute(
        """INSERT INTO message_reader_bodies (
            message_key, content_hash, stored_body_hash, body_text, truncated,
            updated_at, last_seen_at, reader_text_version, source_body_length,
            reader_hash_authoritative
           ) VALUES (?, ?, ?, ?, 1, ?, ?, 2, 10, 0)""",
        ("mid:trunc@ex.com", "c" * 64, "d" * 64, "short", now, now),
    )
    conn.commit()
    report = run_check(conn)
    codes = {i.code for i in report.issues}
    assert "over_cap_body" in codes
    assert "invalid_truncation_relation" in codes
    conn.close()


def test_require_schema_rejects_low_version(tmp_path: Path) -> None:
    db_path = tmp_path / "rollup.db"
    conn = init_db(db_path)
    assert SCHEMA_VERSION >= 9
    require_schema(conn, min_version=9)
    conn.execute("UPDATE schema_version SET version = 8 WHERE id = 1")
    conn.commit()
    with pytest.raises(RuntimeError, match="migration required"):
        require_schema(conn, min_version=9)
    conn.close()
