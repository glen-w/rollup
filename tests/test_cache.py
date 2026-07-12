"""Tests for Ollama summary cache (Ollama phase)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


from rollup.state import (
    SCHEMA_VERSION,
    get_cached_summary,
    get_cached_summary_generation,
    get_schema_version,
    init_db_with_summaries,
    store_summary,
    store_summary_generation,
)
from rollup.summarize import PROMPT_VERSION, build_summary_cache_key_parts


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
    assert (
        get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "short_update") is None
    )
    conn.close()


def test_summary_cache_preserves_multiple_models(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Model A summary", now)
    store_summary(conn, "key1", "hash1", "essay", "other:7b", "Model B summary", now)
    assert (
        get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "essay")
        == "Model A summary"
    )
    assert (
        get_cached_summary(conn, "key1", "hash1", "other:7b", "essay")
        == "Model B summary"
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM summaries WHERE message_key = ?", ("key1",)
    ).fetchone()[0]
    assert count == 2
    conn.close()


def test_summary_cache_preserves_newsletter_types(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary(conn, "key1", "hash1", "essay", "llama3.2:3b", "Essay summary", now)
    store_summary(
        conn, "key1", "hash1", "link_roundup", "llama3.2:3b", "Roundup summary", now
    )
    assert (
        get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "essay")
        == "Essay summary"
    )
    assert (
        get_cached_summary(conn, "key1", "hash1", "llama3.2:3b", "link_roundup")
        == "Roundup summary"
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM summaries WHERE message_key = ?", ("key1",)
    ).fetchone()[0]
    assert count == 2
    conn.close()


def test_init_db_with_summaries_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    assert get_schema_version(conn) == SCHEMA_VERSION == 6
    rows = conn.execute("SELECT id, version FROM schema_version").fetchall()
    assert rows == [(1, SCHEMA_VERSION)]
    conn.close()


def test_rich_summary_cache_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    now = datetime.now().astimezone()
    store_summary_generation(
        conn,
        message_key="key1",
        content_hash="hash1",
        newsletter_type="essay",
        provider="ollama",
        profile_name="deep",
        model="gpt-oss:20b",
        prompt_style="deep",
        prompt_version=PROMPT_VERSION,
        temperature=0.2,
        num_ctx=32768,
        options={"top_p": 0.9},
        summary_input_hash="input-hash-1",
        summary="Deep summary",
        created_at=now,
    )
    cached = get_cached_summary_generation(
        conn,
        message_key="key1",
        content_hash="hash1",
        newsletter_type="essay",
        provider="ollama",
        profile_name="deep",
        model="gpt-oss:20b",
        prompt_style="deep",
        prompt_version=PROMPT_VERSION,
        temperature=0.2,
        num_ctx=32768,
        options={"top_p": 0.9},
        summary_input_hash="input-hash-1",
    )
    assert cached == "Deep summary"
    conn.close()


def test_summary_cache_key_changes_by_profile_model_style_version() -> None:
    common = dict(
        message_key="key1",
        content_hash="hash1",
        newsletter_type="essay",
        provider="ollama",
        profile_name="deep",
        model="gpt-oss:20b",
        prompt_style="deep",
        prompt_version=PROMPT_VERSION,
        temperature=0.2,
        num_ctx=32768,
        options={"top_p": 0.9},
        summary_input_hash="input-hash-1",
    )
    base = build_summary_cache_key_parts(**common)
    assert base == build_summary_cache_key_parts(**common)
    assert base != build_summary_cache_key_parts(**{**common, "profile_name": "rough"})
    assert base != build_summary_cache_key_parts(**{**common, "model": "other:7b"})
    assert base != build_summary_cache_key_parts(**{**common, "prompt_style": "rough"})
    assert base != build_summary_cache_key_parts(
        **{**common, "prompt_version": PROMPT_VERSION + 1}
    )
