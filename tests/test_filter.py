"""Tests for filtering and deduplication."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rollup.config import compute_date_window
from rollup.discovery import iter_mbox_files
from rollup.filter import (
    apply_undated_seen_filter,
    build_digest_entries,
    dedupe_messages,
    split_dated_undated,
)
from rollup.models import ParsedMessage
from rollup.parse import parse_mbox_folder

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"


def _make_parsed(
    key: str,
    date: datetime | None,
    body: str = "body",
    folder: str = "test",
) -> ParsedMessage:
    from rollup.parse import compute_content_hash

    return ParsedMessage(
        message_key=key,
        content_hash=compute_content_hash(body),
        folder_name=folder,
        relative_folder_path=folder,
        subject="subj",
        sender="a@example.com",
        date_raw="",
        date_parsed=date,
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview=body[:100],
        parse_warnings=(),
    )


def test_date_window_inclusive() -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)
    start, end = compute_date_window(now, 7)
    assert start.date() == (now.date() - timedelta(days=6))
    assert end.hour == 23


def test_split_dated_undated() -> None:
    now = datetime.now().astimezone()
    dated = _make_parsed("k1", now)
    undated = _make_parsed("k2", None)
    old = _make_parsed("k3", now - timedelta(days=30))
    start, end = compute_date_window(now, 7)
    in_window, und, skipped = split_dated_undated([dated, undated, old], start, end)
    assert len(in_window) == 1
    assert len(und) == 1
    assert skipped == 1


def test_dedupe_keeps_latest_date() -> None:
    now = datetime.now().astimezone()
    older = _make_parsed("same", now - timedelta(days=1), "short")
    newer = _make_parsed("same", now, "longer body text here")
    deduped, removed = dedupe_messages([older, newer])
    assert removed == 1
    assert deduped[0].body_text == "longer body text here"


def test_undated_seen_filter() -> None:
    from rollup.classify import classify_message
    from rollup.filter import make_digest_entry

    p = _make_parsed("undated-key", None)
    entry = make_digest_entry(classify_message(p), no_ollama=True)
    rendered, skipped = apply_undated_seen_filter([entry], {"undated-key"}, False)
    assert len(rendered) == 0
    assert skipped == 1


def test_dated_not_seen_suppressed() -> None:
    """Dated messages are never filtered by seen_messages."""
    now = datetime.now().astimezone()
    msgs = []
    for folder in iter_mbox_files(FIXTURE_ROOT):
        if folder.folder_name == "hoops":
            parsed, _, _ = parse_mbox_folder(folder, 200_000, 8)
            msgs.extend(parsed)
    dated, undated, skipped, deduped = build_digest_entries(
        msgs, now, 7, no_ollama=True
    )
    assert len(dated) >= 1


def test_build_digest_preview_fallback() -> None:
    now = datetime.now().astimezone()
    p = _make_parsed("k", now, "preview body text")
    from rollup.classify import classify_message
    from rollup.filter import make_digest_entry

    entry = make_digest_entry(classify_message(p), no_ollama=True)
    assert entry.summary_source == "preview_fallback"
    assert entry.summary == p.preview


def test_date_window_boundary_inclusive() -> None:
    tz = datetime.now().astimezone().tzinfo
    anchor = datetime(2026, 7, 1, 12, 0, tzinfo=tz)
    start, end = compute_date_window(anchor, 7)
    at_start = _make_parsed("k1", start)
    at_end = _make_parsed("k2", end)
    dated, undated, skipped = split_dated_undated([at_start, at_end], start, end)
    assert len(dated) == 2
    assert skipped == 0


def test_dedupe_same_date_longer_body() -> None:
    now = datetime.now().astimezone()
    short = _make_parsed("same", now, "short")
    long = _make_parsed("same", now, "much longer body content")
    deduped, removed = dedupe_messages([short, long])
    assert removed == 1
    assert deduped[0].body_text == "much longer body content"


def test_dedupe_folder_tiebreak() -> None:
    now = datetime.now().astimezone()
    a = _make_parsed("same", now, "same length body!!", folder="zebra")
    b = _make_parsed("same", now, "same length body!!", folder="alpha")
    deduped, removed = dedupe_messages([a, b])
    assert removed == 1
    assert deduped[0].folder_name == "alpha"


def test_duplicate_message_id_dedup_deterministic() -> None:
    now = datetime.now().astimezone()
    older = _make_parsed("mid:dup@x.com", now - timedelta(days=2), "old", folder="a")
    newer = _make_parsed("mid:dup@x.com", now, "new", folder="b")
    deduped, removed = dedupe_messages([older, newer])
    assert removed == 1
    assert deduped[0].body_text == "new"
