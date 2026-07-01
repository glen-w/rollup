"""Tests for Ollama summary cache (Ollama phase)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from rollup.state import get_cached_summary, init_db_with_summaries, store_summary


def test_summary_cache_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Summary text", now)
    cached = get_cached_summary(conn, "key1", "hash1")
    assert cached == "Summary text"
    assert get_cached_summary(conn, "key1", "hash2") is None
    conn.close()


def test_summary_cache_hash_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Summary text", now)
    assert get_cached_summary(conn, "key1", "hash2") is None
    conn.close()
