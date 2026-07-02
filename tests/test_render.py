"""Tests for digest rendering."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from rollup.classify import classify_message
from rollup.config import compute_date_window
from rollup.filter import make_digest_entry
from rollup.models import (
    DigestReport,
    DigestStats,
    DigestSummaryMetadata,
    DigestSummaryRouteStat,
    LinkItem,
)
from rollup.parse import compute_content_hash
from rollup.models import ParsedMessage
from rollup.render import (
    atomic_write_digest,
    cleanup_stale_temps,
    render_html,
    render_markdown,
    render_stats_block,
)


def _entry(body: str = "Summary text here"):
    parsed = ParsedMessage(
        message_key="k1",
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Test Subject",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=("https://example.com",),
        link_items=(LinkItem("https://example.com", "Example article", None, 0),),
        read_time_minutes=2,
        preview=body[:100],
        parse_warnings=(),
    )
    return make_digest_entry(classify_message(parsed), no_ollama=True)


def _report() -> DigestReport:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    entry = _entry()
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
        summaries_cache=0,
        summaries_fallback=1,
    )
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=stats,
        summary_metadata=DigestSummaryMetadata(
            mode="type_routed",
            profiles_used=("rough", "deep"),
            models_used=("llama3.2:3b", "gpt-oss:20b"),
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=1,
            summaries_errors=0,
            routing_counts=(
                DigestSummaryRouteStat(
                    newsletter_type="short_update",
                    profile_name="rough",
                    model="llama3.2:3b",
                    count=1,
                ),
            ),
        ),
    )


def test_render_markdown_contains_subject() -> None:
    md = render_markdown(_report(), 8)
    assert "Test Subject" in md
    assert "## tech" in md
    assert "## Summary Routing" in md


def test_render_html_escapes_content() -> None:
    report = _report()
    html = render_html(report, 8)
    assert "<script>" not in html
    assert "Test Subject" in html
    assert "Summary Routing" in html


def test_stats_block() -> None:
    block = render_stats_block(_report().stats)
    assert "Folders scanned: 1" in block
    assert "errors 0" in block


def test_atomic_write(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    md_path, html_path = atomic_write_digest(tmp_path, now, "# Test\n", "<html></html>")
    assert md_path.exists()
    assert html_path.exists()
    assert not list(tmp_path.glob(".tmp-*"))


def test_atomic_write_variant_name(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    md_path, html_path = atomic_write_digest(
        tmp_path, now, "# Test\n", "<html></html>", variant_name="deep"
    )
    assert md_path.name.endswith(".deep.md")
    assert html_path.name.endswith(".deep.html")


def test_cleanup_stale_temps(tmp_path: Path) -> None:
    stale = tmp_path / ".tmp-stale.md"
    stale.write_text("x")
    cleanup_stale_temps(tmp_path)
    assert not stale.exists()


def test_render_dated_sorted_desc() -> None:
    now = datetime.now().astimezone()
    older = _entry("older")
    older_parsed = older.classified.parsed
    newer_parsed = ParsedMessage(
        **{
            **older_parsed.__dict__,
            "subject": "Newer",
            "date_parsed": now,
            "message_key": "k2",
            "content_hash": compute_content_hash("newer"),
            "body_text": "newer",
            "preview": "newer",
        }
    )
    older_dt = ParsedMessage(
        **{
            **older_parsed.__dict__,
            "date_parsed": now - timedelta(days=1),
        }
    )
    older_entry = make_digest_entry(classify_message(older_dt), no_ollama=True)
    newer_entry = make_digest_entry(classify_message(newer_parsed), no_ollama=True)
    start, end = compute_date_window(now, 7)
    stats = DigestStats(
        folders_scanned=1,
        messages_parsed=2,
        dated_included=2,
        undated_needing_review=0,
        skipped_outside_window=0,
        skipped_seen_undated=0,
        deduped_messages=0,
        parse_errors=0,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=2,
    )
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (newer_entry, older_entry)},
        undated=(),
        stats=stats,
    )
    md = render_markdown(report, 8)
    assert md.index("Newer") < md.index("Test Subject")


def test_render_undated_section() -> None:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    undated_parsed = ParsedMessage(
        message_key="u1",
        content_hash=compute_content_hash("undated body"),
        folder_name="misc",
        relative_folder_path="misc",
        subject="Undated Subj",
        sender="u@example.com",
        date_raw="",
        date_parsed=None,
        body_text="undated body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="undated body",
        parse_warnings=(),
    )
    undated_entry = make_digest_entry(classify_message(undated_parsed), no_ollama=True)
    stats = DigestStats(
        folders_scanned=1,
        messages_parsed=1,
        dated_included=0,
        undated_needing_review=1,
        skipped_outside_window=0,
        skipped_seen_undated=0,
        deduped_messages=0,
        parse_errors=0,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=1,
    )
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={},
        undated=(undated_entry,),
        stats=stats,
    )
    md = render_markdown(report, 8)
    html = render_html(report, 8)
    assert "Undated Subj" in md
    assert "id='undated'" in html
    assert "Undated Subj" in html


def test_render_html_summary_pre_wrap() -> None:
    body = "Line one\n\nLine two"
    entry = _entry(body)
    entry = make_digest_entry(
        entry.classified,
        no_ollama=True,
        summary="Para one\n\nPara two",
        summary_source="preview_fallback",
    )
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
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
        summaries_cache=0,
        summaries_fallback=1,
    )
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=stats,
    )
    html = render_html(report, 8)
    assert "white-space:pre-wrap" in html
    assert "Para one" in html


def test_render_clickable_links() -> None:
    md = render_markdown(_report(), 8)
    html = render_html(_report(), 8)
    assert "[Example article](https://example.com)" in md
    assert 'href="https://example.com"' in html
    assert ">Example article<" in html


def test_render_hides_raw_url_as_visible_text() -> None:
    parsed = ParsedMessage(
        message_key="k-raw",
        content_hash=compute_content_hash("raw body"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Raw URL Link",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="raw body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(
            "https://substack.com/app-link/post?publication_id=1&post_id=2&token=abc",
        ),
        link_items=(
            LinkItem(
                "https://substack.com/app-link/post?publication_id=1&post_id=2&token=abc",
                "https://substack.com/app-link/post?publication_id=1&post_id=2&token=abc",
                None,
                0,
            ),
        ),
        read_time_minutes=1,
        preview="raw body",
        parse_warnings=(),
    )
    report = _report()
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    report = DigestReport(
        generated_at=report.generated_at,
        lookback_days=report.lookback_days,
        window_start=report.window_start,
        window_end=report.window_end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=report.stats,
    )
    md = render_markdown(report, 8)
    html = render_html(report, 8)
    assert (
        "[Open post](https://substack.com/app-link/post?publication_id=1&post_id=2&token=abc)"
        in md
    )
    assert ">https://substack.com/app-link/post?" not in html


def test_render_markdown_escapes_link_labels_without_changing_href() -> None:
    parsed = ParsedMessage(
        message_key="k-escape",
        content_hash=compute_content_hash("escape body"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Escaped Label",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="escape body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=("https://example.com/report?x=1&y=2",),
        link_items=(
            LinkItem("https://example.com/report?x=1&y=2", "[Report]", None, 0),
        ),
        read_time_minutes=1,
        preview="escape body",
        parse_warnings=(),
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    base = _report()
    report = DigestReport(
        generated_at=base.generated_at,
        lookback_days=base.lookback_days,
        window_start=base.window_start,
        window_end=base.window_end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=base.stats,
    )
    md = render_markdown(report, 8)
    html = render_html(report, 8)
    assert r"[\[Report\]](https://example.com/report?x=1&y=2)" in md
    assert 'href="https://example.com/report?x=1&amp;y=2"' in html
    assert ">[Report]<" in html


def test_render_wrapper_links_keep_clean_visible_text_and_original_hrefs() -> None:
    parsed = ParsedMessage(
        message_key="k-wrapper",
        content_hash=compute_content_hash("wrapper body"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Wrapped Links",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="wrapper body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(
            "https://u14608870.ct.sendgrid.net/ls/click?upn=report123",
            "https://u14608870.ct.sendgrid.net/ls/click?upn=register123",
            "https://newsletter.substack.com/c/article123",
        ),
        link_items=(
            LinkItem(
                "https://u14608870.ct.sendgrid.net/ls/click?upn=report123",
                "Report",
                "Annual report download",
                0,
            ),
            LinkItem(
                "https://u14608870.ct.sendgrid.net/ls/click?upn=register123",
                "Register",
                "Register for the webinar",
                1,
            ),
            LinkItem(
                "https://newsletter.substack.com/c/article123",
                "Article",
                "Featured story",
                2,
            ),
        ),
        read_time_minutes=1,
        preview="wrapper body",
        parse_warnings=(),
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    base = _report()
    report = DigestReport(
        generated_at=base.generated_at,
        lookback_days=base.lookback_days,
        window_start=base.window_start,
        window_end=base.window_end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=base.stats,
    )
    md = render_markdown(report, 8)
    html = render_html(report, 8)
    assert "[Report](https://u14608870.ct.sendgrid.net/ls/click?upn=report123)" in md
    assert (
        "[Register](https://u14608870.ct.sendgrid.net/ls/click?upn=register123)" in md
    )
    assert "[Article](https://newsletter.substack.com/c/article123)" in md
    assert ">Report<" in html
    assert ">Register<" in html
    assert ">Article<" in html
    assert 'href="https://u14608870.ct.sendgrid.net/ls/click?upn=report123"' in html
    assert 'href="https://u14608870.ct.sendgrid.net/ls/click?upn=register123"' in html
    assert 'href="https://newsletter.substack.com/c/article123"' in html


def test_render_other_links_in_secondary_section_only() -> None:
    parsed = ParsedMessage(
        message_key="k-other",
        content_hash=compute_content_hash("other body"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Other Links",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="other body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(
            "https://example.com/post/1",
            "https://example.com/post/2",
            "https://example.com/post/3",
            "https://example.com/post/4",
            "https://example.com/post/5",
            "https://calendar.google.com/calendar/event?action=VIEW",
        ),
        link_items=(
            LinkItem("https://example.com/post/1", "Read article 1", None, 0),
            LinkItem("https://example.com/post/2", "Read article 2", None, 1),
            LinkItem("https://example.com/post/3", "Read article 3", None, 2),
            LinkItem("https://example.com/post/4", "Read article 4", None, 3),
            LinkItem("https://example.com/post/5", "Read article 5", None, 4),
            LinkItem(
                "https://calendar.google.com/calendar/event?action=VIEW", None, None, 5
            ),
        ),
        read_time_minutes=1,
        preview="other body",
        parse_warnings=(),
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    base = _report()
    report = DigestReport(
        generated_at=base.generated_at,
        lookback_days=base.lookback_days,
        window_start=base.window_start,
        window_end=base.window_end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=base.stats,
    )
    md = render_markdown(report, 8)
    html = render_html(report, 8)
    assert "**Other links:**" in md
    assert "View calendar event" in md
    assert "<summary>Other links</summary>" in html


def test_atomic_write_failure_cleans_partials(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    date_str = now.strftime("%Y-%m-%d")
    original_rename = Path.rename

    def fail_on_html_rename(self, target):
        if str(target).endswith(".html"):
            raise OSError("simulated html rename failure")
        return original_rename(self, target)

    with patch.object(Path, "rename", fail_on_html_rename):
        with pytest.raises(OSError, match="simulated"):
            atomic_write_digest(tmp_path, now, "# Test\n", "<html></html>")

    assert not (tmp_path / f"{date_str}-newsletter-digest.md").exists()
    assert not (tmp_path / f"{date_str}-newsletter-digest.html").exists()
    assert not list(tmp_path.glob(".tmp-*"))
