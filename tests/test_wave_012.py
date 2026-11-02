"""0.12 wave: RSS ingest, Gmail history delta, continuity, Kindle."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rollup.continuity import classify_entry, record_continuity
from rollup.coverage.normalize import canonical_url
from rollup.gmail.cache import apply_history_delta
from rollup.gmail.session import GmailClient, GmailHistoryExpired, GmailSessionError
from rollup.models import (
    ClassifiedMessage,
    DigestEntry,
    DigestReport,
    DigestStats,
    ParsedMessage,
)
from rollup.pipeline.status import evaluate_no_input
from rollup.pipeline.types import DiscoveryResult, ParseCounts, ParseResult, StageWarning
from rollup.rss.parse import RssParseError, parse_feed_xml, reject_dangerous_xml
from rollup.state import SCHEMA_VERSION, get_schema_version, init_db


def test_schema_v19_tables(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION == 19
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"rss_feeds", "rss_items", "briefing_continuity", "digest_attempts"} <= names


def test_rss_rejects_doctype() -> None:
    with pytest.raises(RssParseError):
        reject_dangerous_xml("<!DOCTYPE foo [<!ENTITY x 'y'>]><rss></rss>")


def test_rss_parses_item_and_keeps_undated() -> None:
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>Hello &amp; world</title><link>https://example.com/a</link>
        <pubDate>Mon, 02 Nov 2026 12:00:00 +0000</pubDate>
        <description><![CDATA[<p>Body text here</p>]]></description>
      </item>
    </channel></rss>"""
    items = parse_feed_xml(xml, feed_url="https://example.com/feed")
    assert len(items) == 1
    assert items[0].title.startswith("Hello")
    assert "Body text" in items[0].body_text
    assert items[0].item_key.startswith("rss:item:")


def test_gmail_client_refuses_non_get() -> None:
    client = GmailClient(Path("/tmp/does-not-matter-token.json"))
    with pytest.raises(GmailSessionError, match="refuses POST"):
        client._request("POST", "https://example.invalid", headers={}, label="nope")
    src = inspect.getsource(GmailClient)
    assert "session.post" not in src
    assert "/batch" not in src


def test_history_delta_add_and_delete() -> None:
    ids = apply_history_delta(
        ["old"],
        [
            {"messagesAdded": [{"message": {"id": "new", "labelIds": ["L"]}}]},
            {"messagesDeleted": [{"message": {"id": "old"}}]},
        ],
        label_id="L",
        limit=10,
    )
    assert ids == ["new"]


def test_history_expired_is_not_found() -> None:
    assert issubclass(GmailHistoryExpired, Exception)


def _entry(key: str, subject: str, url: str, folder: str = "News") -> DigestEntry:
    parsed = ParsedMessage(
        message_key=key,
        content_hash="abc",
        folder_name=folder,
        relative_folder_path=folder,
        subject=subject,
        sender="Ada",
        date_raw="",
        date_parsed=datetime.now(timezone.utc),
        body_text="body",
        body_html=None,
        html_heading_count=0,
        html_link_count=1,
        html_section_break_count=0,
        links=(url,),
        link_items=(),
        read_time_minutes=1,
        preview="body",
        parse_warnings=(),
    )
    return DigestEntry(
        classified=ClassifiedMessage(parsed=parsed, newsletter_type="article", classification_scores=()),
        summary=None,
        summary_source="preview",
    )


def test_continuity_repeat_and_continuing(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    url = "https://example.com/story"
    entry = _entry("m1", "A story worth reading", url)
    stats = DigestStats(
        folders_scanned=1,
        messages_parsed=1,
        dated_included=1,
        undated_needing_review=0,
        skipped_outside_window=0,
        skipped_seen_undated=0,
        deduped_messages=0,
        parse_errors=0,
        summaries_ollama=0,
        summaries_litellm=0,
        summaries_cache=0,
        summaries_fallback=1,
        summaries_errors=0,
    )
    when = datetime.now(timezone.utc)
    report = DigestReport(
        generated_at=when,
        lookback_days=7,
        window_start=when - timedelta(days=6),
        window_end=when,
        dated_by_folder={"News": (entry,)},
        undated=(),
        stats=stats,
    )
    record_continuity(conn, report, run_id="run-1")
    from rollup.continuity import ContinuityRow, load_prior_rows

    rows = load_prior_rows(conn)
    assert rows[0].canonical_url == canonical_url(url)
    assert classify_entry(entry, rows, prev_run="run-1") == "repeat"
    older = ContinuityRow(
        run_id="run-0",
        window_end="2020-01-01T00:00:00+00:00",
        message_key="m0",
        canonical_url=canonical_url(url),
        title_norm="a story worth reading",
        folder_name="News",
    )
    assert classify_entry(entry, rows + [older], prev_run="run-1") == "continuing"
    conn.close()


def test_rss_only_failure_is_hard() -> None:
    discovery = DiscoveryResult(folders=(), rss_feeds=("feed",))
    parse = ParseResult(
        messages=(),
        counts=ParseCounts(messages_seen=0, messages_parsed=0),
        warnings=(StageWarning(code="rss_fetch_failed", message="boom"),),
    )
    reason = evaluate_no_input(folders_include=(), discovery=discovery, parse=parse)
    assert reason and "RSS fetch failed" in reason


def test_kindle_writer_is_optional() -> None:
    from rollup.addons.kindle.writer import KindleOutputWriter
    from rollup.output_writers import writer_is_required

    assert writer_is_required(KindleOutputWriter()) is False
