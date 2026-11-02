"""Paginated registry listing and override parsing after the listing split."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.models import ParsedMessage
from rollup.source_registry import (
    SourceRegistryError,
    SourceRegistryPage,
    list_source_registry_page,
    observe_sources,
    parse_override_updates,
    set_overrides,
)
from rollup.source_registry_listing import (
    list_source_registry_page as listing_page,
)
from rollup.source_registry_listing import (
    parse_override_updates as listing_parse,
)
from rollup.state import init_db


def _msg(key: str, *, source_key: str, sender: str) -> ParsedMessage:
    return ParsedMessage(
        message_key=key,
        content_hash="h",
        folder_name="tech",
        relative_folder_path="tech",
        subject="Subj",
        sender=sender,
        date_raw="Tue, 01 Jul 2026 12:00:00 +0000",
        date_parsed=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        body_text="body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="body",
        parse_warnings=(),
        source_key=source_key,
        list_id=None,
    )


def test_listing_helpers_are_reexported() -> None:
    assert list_source_registry_page is listing_page
    assert parse_override_updates is listing_parse


def test_parse_override_updates_invalid_and_inherit() -> None:
    with pytest.raises(SourceRegistryError, match="invalid boolean"):
        parse_override_updates(fields={"enabled"}, values={"enabled": "maybe"})
    with pytest.raises(SourceRegistryError, match="No policy fields"):
        parse_override_updates(fields={"unknown"}, values={"unknown": "x"})
    inherited = parse_override_updates(
        fields={"enabled", "priority", "notes"},
        values={"enabled": "inherit", "priority": "", "notes": "  "},
    )
    assert inherited == {"enabled": None, "priority": None, "notes": None}


def test_list_source_registry_page_sort_and_filter(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime.now().astimezone()
    observe_sources(
        conn,
        [_msg("mid:a", source_key="from:a@b.co", sender="A <a@b.co>")],
        generated_at=now,
    )
    observe_sources(
        conn,
        [_msg("mid:z", source_key="from:z@b.co", sender="Z <z@b.co>")],
        generated_at=now,
    )
    set_overrides(conn, "from:z@b.co", updates={"enabled": False, "priority": 9})

    with pytest.raises(SourceRegistryError, match="invalid sort"):
        list_source_registry_page(conn, sort="not-a-sort")
    with pytest.raises(SourceRegistryError, match="invalid filter"):
        list_source_registry_page(conn, enabled_filter="nope")

    all_page = list_source_registry_page(conn, page_size=1, sort="key_asc")
    assert isinstance(all_page, SourceRegistryPage)
    assert all_page.has_next is True
    assert all_page.total is None
    assert all_page.rows[0].source_key == "from:a@b.co"

    enabled = list_source_registry_page(conn, enabled_filter="enabled")
    disabled = list_source_registry_page(conn, enabled_filter="disabled")
    assert {r.source_key for r in enabled.rows} == {"from:a@b.co"}
    assert {r.source_key for r in disabled.rows} == {"from:z@b.co"}

    searched = list_source_registry_page(conn, q="z@b")
    assert [r.source_key for r in searched.rows] == ["from:z@b.co"]
    conn.close()
