"""Tests for seen_messages state."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from rollup.state import init_db, load_seen_keys, upsert_seen_keys


def test_seen_messages_upsert_and_load(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now().astimezone()
    upsert_seen_keys(conn, ["key1", "key2"], now)
    keys = load_seen_keys(conn)
    assert keys == {"key1", "key2"}
    conn.close()


def test_dry_run_no_db_required(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    assert not db.exists()
