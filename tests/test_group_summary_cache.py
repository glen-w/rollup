"""Schema v6 group-summary cache migration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rollup.state import (
    SCHEMA_VERSION,
    get_group_summary_generation,
    get_schema_version,
    init_db_with_summaries,
    store_group_summary_generation,
    store_summary_generation,
    get_cached_summary_generation,
)


def test_schema_version_is_six(tmp_path: Path) -> None:
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    assert get_schema_version(conn) == 8
    assert SCHEMA_VERSION == 8
    conn.close()


def test_entry_cache_survives_v6_init(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db_with_summaries(db)
    store_summary_generation(
        conn,
        message_key="mid:1",
        content_hash="ch",
        newsletter_type="essay",
        provider="ollama",
        profile_name="default",
        model="m",
        prompt_style="default",
        prompt_version=3,
        temperature=0.1,
        num_ctx=4096,
        options={},
        summary_input_hash="sih",
        summary="cached summary",
        created_at=datetime.now(timezone.utc),
    )
    conn.close()

    conn2 = init_db_with_summaries(db)
    assert get_schema_version(conn2) == 8
    hit = get_cached_summary_generation(
        conn2,
        message_key="mid:1",
        content_hash="ch",
        newsletter_type="essay",
        provider="ollama",
        profile_name="default",
        model="m",
        prompt_style="default",
        prompt_version=3,
        temperature=0.1,
        num_ctx=4096,
        options={},
        summary_input_hash="sih",
    )
    assert hit == "cached summary"
    store_group_summary_generation(
        conn2,
        cache_key="abc",
        summary="group blurb",
        created_at=datetime.now(timezone.utc),
    )
    assert get_group_summary_generation(conn2, cache_key="abc") == "group blurb"
    conn2.close()
