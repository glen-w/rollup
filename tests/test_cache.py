"""Tests for Ollama summary cache (Ollama phase)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from rollup.state import (
    SCHEMA_VERSION,
    get_cached_summary,
    init_db_with_summaries,
    store_summary,
)


def test_summary_cache_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Summary text", now)
    cached = get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "essay")
    assert cached == "Summary text"
    assert get_cached_summary(conn, "key1", "hash2", "llama3.2:3b", "essay") is None
    conn.close()


def test_summary_cache_hash_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Summary text", now)
    assert get_cached_summary(conn, "key1", "hash2", "llama3.2:3b", "essay") is None
    conn.close()


def test_summary_cache_model_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Summary text", now)
    assert get_cached_summary(conn, "key1", "hash1", "other:7b", "essay") is None
    conn.close()


def test_summary_cache_type_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Summary text", now)
    assert get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "short_update") is None
    conn.close()


def test_summary_cache_preserves_multiple_models(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Model A summary", now)
    store_summary(conn, "key1", "hash1", "essay", "other:7b", "Model B summary", now)
    assert get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "essay") == "Model A summary"
    assert get_cached_summary(conn, "key1", "hash1", "other:7b", "essay") == "Model B summary"
    count = conn.execute("SELECT COUNT(*) FROM summaries WHERE message_key = ?", ("key1",)).fetchone()[0]
    assert count == 2
    conn.close()


def test_summary_cache_preserves_newsletter_types(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Essay summary", now)
    store_summary(conn, "key1", "hash1", "link_roundup", "llama3.2:3b", "Roundup summary", now)
    assert get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "essay") == "Essay summary"
    assert (
        get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "link_roundup")
        == "Roundup summary"
    )
    count = conn.execute("SELECT COUNT(*) FROM summaries WHERE message_key = ?", ("key1",)).fetchone()[0]
    assert count == 2
    conn.close()


def test_init_db_with_summaries_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    assert row is not None
    assert row[0] == SCHEMA_VERSION == 2
    conn.close()
