"""Tests for digest rendering."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from rollup.classify import classify_message
from rollup.config import compute_date_window
from rollup.filter import make_digest_entry
from rollup.final_review import format_final_review_digest_summary
from rollup.models import (
    DigestEntry,
    DigestReport,
    DigestStats,
    DigestSummaryMetadata,
    DigestSummaryRouteStat,
    FinalReviewIssue,
    FinalReviewResult,
    LinkItem,
    ParsedMessage,
)
from rollup.parse import compute_content_hash
from rollup.render import (
    _folder_section_id,
    _format_newsletter_type,
    _format_section_byline,
    _format_window_range,
    _sort_entries_by_read_time,
    atomic_write_digest,
    cleanup_stale_temps,
    digest_output_stem,
    render_html,
    render_markdown,
    render_stats_block,
    write_branding_assets,
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
    assert "## 💻 tech" in md
    assert "## Digest generation details" in md
    assert "### Summary routing" in md
    assert md.index("## Contents") < md.index("## 💻 tech")
    assert md.index("## 💻 tech") < md.index("## Digest generation details")
    assert "# Rollup —" in md
    assert "![Rollup](rollup_logo.png)" in md


def test_render_html_includes_branding() -> None:
    html = render_html(_report(), 8)
    assert "<title>Rollup —" in html
    assert "rollup_logo.png" in html
    assert "favicon.ico" in html
    assert "class='rollup-logo'" in html
    assert "height:120px" in html
    assert "<h1>" not in html


def test_write_branding_assets(tmp_path: Path) -> None:
    write_branding_assets(tmp_path)
    assert (tmp_path / "rollup_logo.png").is_file()
    assert (tmp_path / "favicon.ico").is_file()
    assert (tmp_path / "favicon.ico").stat().st_size > 0


def test_atomic_write_includes_branding_assets(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    atomic_write_digest(tmp_path, now, "# Test\n", "<html></html>")
    assert (tmp_path / "rollup_logo.png").is_file()
    assert (tmp_path / "favicon.ico").is_file()


def test_render_uses_folder_and_read_time_emojis() -> None:
    md = render_markdown(_report(), 8)
    html = render_html(_report(), 8)
    assert "🕐 2 min" in md
    assert "🕐 2 min" in html
    assert "**Folder:** 💻 tech" in md


def test_render_html_escapes_content() -> None:
    report = _report()
    html = render_html(report, 8)
    assert "<script>alert" not in html
    assert "Test Subject" in html
    assert "Summary routing" in html
    assert "<details class='run-details'>" in html


def test_render_html_title_bar_omits_sender_email() -> None:
    parsed = ParsedMessage(
        message_key="k1",
        content_hash=compute_content_hash("body"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Welcome to Can't Get Much Higher!",
        sender="Chris Dalla Riva <chrisdallariva@substack.com>",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=3,
        preview="body",
        parse_warnings=(),
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=_report().stats,
    )
    html = render_html(report, 8)
    assert "Chris Dalla Riva" in html
    assert "chrisdallariva@substack.com" not in html
    assert "<strong>Welcome to Can&#x27;t Get Much Higher!</strong>" in html
    assert " — 20" not in html.split("<summary>")[1].split("</summary>")[0]


def test_format_window_range_same_month() -> None:
    start = datetime(2026, 7, 23, tzinfo=datetime.now().astimezone().tzinfo)
    end = datetime(2026, 7, 30, tzinfo=start.tzinfo)
    assert _format_window_range(start, end) == "23-30 July 2026"


def test_format_window_range_cross_month() -> None:
    tz = datetime.now().astimezone().tzinfo
    start = datetime(2026, 7, 30, tzinfo=tz)
    end = datetime(2026, 8, 6, tzinfo=tz)
    assert _format_window_range(start, end) == "30 July - 6 August 2026"


def test_render_html_uses_window_range_subhead() -> None:
    html = render_html(_report(), 8)
    assert "Week of" not in html
    assert "class='rollup-subhead'" in html
    assert "newsletters</em></p>" in html


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


def test_atomic_write_includes_timestamp_in_filename(tmp_path: Path) -> None:
    now = datetime(2026, 7, 2, 14, 30, 52, tzinfo=datetime.now().astimezone().tzinfo)
    md_path, _html_path = atomic_write_digest(tmp_path, now, "# Test\n", "<html></html>")
    # Stem uses UTC (Z) so local 14:30 CEST → 12:30Z
    assert md_path.name.endswith("-newsletter-digest.md")
    assert "T" in md_path.name and "Z-" in md_path.name or md_path.name.endswith("Z-newsletter-digest.md")
    from datetime import timezone

    utc = now.astimezone(timezone.utc)
    expected = utc.strftime("%Y-%m-%dT%H%M%SZ") + "-newsletter-digest.md"
    assert md_path.name == expected


def test_atomic_write_with_run_id_suffix(tmp_path: Path) -> None:
    now = datetime(2026, 7, 2, 14, 30, 52, tzinfo=datetime.now().astimezone().tzinfo)
    md_path, _html_path = atomic_write_digest(
        tmp_path, now, "# Test\n", "<html></html>", run_id_short="a1b2c3d4"
    )
    from datetime import timezone

    utc = now.astimezone(timezone.utc)
    expected = utc.strftime("%Y-%m-%dT%H%M%SZ") + "-a1b2c3d4-newsletter-digest.md"
    assert md_path.name == expected


def test_atomic_write_same_day_does_not_overwrite(tmp_path: Path) -> None:
    tz = datetime.now().astimezone().tzinfo
    first = datetime(2026, 7, 2, 9, 0, 0, tzinfo=tz)
    second = datetime(2026, 7, 2, 17, 30, 0, tzinfo=tz)
    atomic_write_digest(tmp_path, first, "# First\n", "<html>first</html>")
    atomic_write_digest(tmp_path, second, "# Second\n", "<html>second</html>")
    md_files = sorted(tmp_path.glob("*-newsletter-digest.md"))
    assert len(md_files) == 2
    assert md_files[0].read_text(encoding="utf-8") == "# First\n"
    assert md_files[1].read_text(encoding="utf-8") == "# Second\n"


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
    assert "<p>Para one</p>" in html
    assert "<p>Para two</p>" in html


def test_render_html_summary_renders_markdown_links() -> None:
    entry = _entry()
    entry = make_digest_entry(
        entry.classified,
        no_ollama=True,
        summary=(
            "- RADAR Festival returns to Manchester.\n"
            "- Get Tickets: [RADAR Festival](https://example.com/tickets?x=1&y=2)"
        ),
        summary_source="preview_fallback",
    )
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=_report().stats,
    )
    html = render_html(report, 8)
    assert 'href="https://example.com/tickets?x=1&amp;y=2"' in html
    assert ">RADAR Festival</a>" in html
    assert 'target="_blank"' in html
    assert "[RADAR Festival](https://example.com/tickets" not in html


def test_render_html_summary_renders_italic_markdown() -> None:
    entry = _entry()
    entry = make_digest_entry(
        entry.classified,
        no_ollama=True,
        summary=(
            "Overview: A summer reading list featuring *Don Quixote* and "
            "*The Adventures of Sherlock Holmes*, plus **must-read** picks."
        ),
        summary_source="preview_fallback",
    )
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=_report().stats,
    )
    html = render_html(report, 8)
    assert "<em>Don Quixote</em>" in html
    assert "<em>The Adventures of Sherlock Holmes</em>" in html
    assert "<strong>must-read</strong>" in html
    assert "*Don Quixote*" not in html
    assert "*The Adventures of Sherlock Holmes*" not in html


def test_render_html_summary_renders_markdown() -> None:
    entry = _entry()
    entry = make_digest_entry(
        entry.classified,
        no_ollama=True,
        summary="### Key Themes:\n\n- **Victor Wembanyama's Development:**\n- Second point",
        summary_source="preview_fallback",
    )
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=_report().stats,
    )
    html = render_html(report, 8)
    assert "<h3>Key Themes:</h3>" in html
    assert "<strong>Victor Wembanyama&#x27;s Development:</strong>" in html
    assert "### Key Themes:" not in html
    assert "**Victor Wembanyama" not in html


def test_render_strips_trailing_worth_opening_section() -> None:
    entry = _entry()
    entry = make_digest_entry(
        entry.classified,
        no_ollama=True,
        summary=(
            "Overview of the newsletter.\n\n"
            "Worth opening? Yes, if you enjoy exploring themes of male friendship "
            "through classic and contemporary literature."
        ),
        summary_source="cache",
    )
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=_report().stats,
    )
    html = render_html(report, 8)
    md = render_markdown(report, 8)
    assert "Overview of the newsletter." in html
    assert "Overview of the newsletter." in md
    assert "Worth opening?" not in html
    assert "Worth opening?" not in md
    assert "male friendship" not in html


def test_render_clickable_links() -> None:
    md = render_markdown(_report(), 8)
    html = render_html(_report(), 8)
    assert "[Example article](https://example.com)" in md
    assert 'href="https://example.com"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert ">Example article<" in html


def test_render_html_item_type_in_card_body() -> None:
    report = _report()
    html = render_html(report, 8)
    ntype = report.dated_by_folder["tech"][0].classified.newsletter_type
    summary = html.split("<summary>")[1].split("</summary>")[0]
    assert ntype not in summary
    assert (
        f"<p class='item-type'>{_format_newsletter_type(ntype)}</p>" in html
    )
    assert "<strong>Folder:</strong>" not in html


def test_format_newsletter_type() -> None:
    assert _format_newsletter_type("short_update") == "Short update"
    assert _format_newsletter_type("link_roundup") == "Link roundup"


def test_render_html_folder_accent_classes() -> None:
    html = render_html(_report(), 8)
    assert "class='folder-section folder-accent-tech'" in html
    assert ".folder-accent-tech>h2{border-left:4px solid #4a7fd4" in html
    assert ".folder-accent-tech .newsletter-card{border-color:#4a7fd4" in html


def test_render_html_sorts_entries_by_read_time() -> None:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)

    def make_entry(subject: str, minutes: int, message_key: str) -> DigestEntry:
        body = f"{subject} body"
        parsed = ParsedMessage(
            message_key=message_key,
            content_hash=compute_content_hash(body),
            folder_name="tech",
            relative_folder_path="tech",
            subject=subject,
            sender="a@example.com",
            date_raw="",
            date_parsed=now,
            body_text=body,
            body_html=None,
            html_heading_count=0,
            html_link_count=0,
            html_section_break_count=0,
            links=(),
            link_items=(),
            read_time_minutes=minutes,
            preview=body,
            parse_warnings=(),
        )
        return make_digest_entry(classify_message(parsed), no_ollama=True)

    entries = (
        make_entry("Five min read", 5, "k5"),
        make_entry("One min read", 1, "k1"),
        make_entry("Three min read", 3, "k3"),
    )
    assert [e.classified.parsed.subject for e in _sort_entries_by_read_time(entries)] == [
        "One min read",
        "Three min read",
        "Five min read",
    ]
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": entries},
        undated=(),
        stats=_report().stats,
    )
    html = render_html(report, 8)
    section_start = html.index("id='folder-tech'")
    section_end = html.index("</section>", section_start)
    section_html = html[section_start:section_end]
    assert (
        section_html.index("One min read")
        < section_html.index("Three min read")
        < section_html.index("Five min read")
    )


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
        "[Register for the webinar](https://u14608870.ct.sendgrid.net/ls/click?upn=register123)"
        in md
    )
    assert "[Article](https://newsletter.substack.com/c/article123)" in md
    assert ">Report<" in html
    assert ">Register for the webinar<" in html
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


def _multi_folder_report() -> DigestReport:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)

    def make_entry(
        folder: str,
        subject: str,
        message_key: str,
        date_parsed: datetime | None,
    ) -> DigestEntry:
        body = f"{subject} body"
        parsed = ParsedMessage(
            message_key=message_key,
            content_hash=compute_content_hash(body),
            folder_name=folder,
            relative_folder_path=folder,
            subject=subject,
            sender="a@example.com",
            date_raw="",
            date_parsed=date_parsed,
            body_text=body,
            body_html=None,
            html_heading_count=0,
            html_link_count=0,
            html_section_break_count=0,
            links=(),
            link_items=(),
            read_time_minutes=1,
            preview=body,
            parse_warnings=(),
        )
        return make_digest_entry(classify_message(parsed), no_ollama=True)

    enviro_entries = tuple(
        make_entry("enviro", f"Enviro {index}", f"env-{index}", now - timedelta(days=index))
        for index in range(3)
    )
    tech_entries = tuple(
        make_entry("tech", f"Tech {index}", f"tech-{index}", now - timedelta(hours=index))
        for index in range(2)
    )
    stats = DigestStats(
        folders_scanned=2,
        messages_parsed=5,
        dated_included=5,
        undated_needing_review=0,
        skipped_outside_window=0,
        skipped_seen_undated=0,
        deduped_messages=0,
        parse_errors=0,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=5,
    )
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"enviro": enviro_entries, "tech": tech_entries},
        undated=(),
        stats=stats,
    )


def test_render_run_details_collapsed_by_default() -> None:
    html = render_html(_report(), 8)
    assert "<details class='run-details'>" in html
    assert "<summary>Digest generation details</summary>" in html
    assert "<h3 class='run-details-heading'>Stats</h3>" in html
    assert "<div class='stats'>" in html
    run_details_pos = html.index("<details class='run-details'>")
    stats_pos = html.index("<h3 class='run-details-heading'>Stats</h3>")
    assert run_details_pos < stats_pos
    assert html.index("</details>", run_details_pos) > stats_pos
    script_pos = html.index("<script>")
    assert run_details_pos < script_pos


def test_render_toc_lists_folders_with_counts() -> None:
    report = _multi_folder_report()
    html = render_html(report, 8)
    assert "class='rollup-toc'" in html
    assert "href='#folder-enviro'>🌲 enviro (3)" in html
    assert "href='#folder-tech'>💻 tech (2)" in html


def test_render_html_section_byline() -> None:
    report = _multi_folder_report()
    html = render_html(report, 8)
    enviro_start = html.index("id='folder-enviro'")
    enviro_end = html.index("</section>", enviro_start)
    enviro_section = html[enviro_start:enviro_end]
    assert "<p class='folder-byline'><em>3 newsletters, 3 minutes reading time</em></p>" in enviro_section
    tech_start = html.index("id='folder-tech'")
    tech_end = html.index("</section>", tech_start)
    tech_section = html[tech_start:tech_end]
    assert "<p class='folder-byline'><em>2 newsletters, 2 minutes reading time</em></p>" in tech_section


def test_format_section_byline_singular() -> None:
    assert _format_section_byline((_entry(),)) == "1 newsletter, 2 minutes reading time"


def test_toc_counts_match_rendered_cards() -> None:
    report = _multi_folder_report()
    html = render_html(report, 8)
    for folder, entries in report.dated_by_folder.items():
        section_id = _folder_section_id(folder)
        section_start = html.index(f"id='{section_id}'")
        section_end = html.index("</section>", section_start)
        section_html = html[section_start:section_end]
        assert section_html.count("data-newsletter-card") == len(entries)


def test_folder_section_id_ignores_emoji_in_display_name() -> None:
    assert _folder_section_id("enviro") == "folder-enviro"
    assert _folder_section_id("tech") == "folder-tech"


def test_folder_section_ids_are_stable() -> None:
    report = _multi_folder_report()
    html_one = render_html(report, 8)
    html_two = render_html(report, 8)
    assert html_one.count("id='folder-enviro'") == 1
    assert html_one.count("id='folder-tech'") == 1
    assert "id='folder-enviro'" in html_two
    assert "id='folder-tech'" in html_two


def test_newsletter_cards_have_marker_class() -> None:
    html = render_html(_report(), 8)
    assert "class='newsletter-card' data-newsletter-card" in html


def test_expand_collapse_controls_present() -> None:
    html = render_html(_report(), 8)
    assert "id='expand-all-cards'" in html
    assert "id='collapse-all-cards'" in html
    assert "aria-label='Expand all newsletter cards'" in html
    script = html.split("<script>")[1].split("</script>")[0]
    assert "querySelectorAll('details.newsletter-card')" in script
    assert "run-details" not in script


def test_all_newsletter_cards_closed_by_default() -> None:
    report = _multi_folder_report()
    html = render_html(report, 8)
    assert "data-newsletter-card open" not in html
    assert html.count("data-newsletter-card") == 5


def test_render_markdown_minimal_toc_and_deferred_run_details() -> None:
    md = render_markdown(_report(), 8)
    assert "## Contents" in md
    assert "- 💻 tech (1)" in md
    assert "## Digest generation details" in md
    assert "Folders scanned:" in md
    assert md.index("## Contents") < md.index("## 💻 tech")
    assert md.index("## 💻 tech") < md.index("## Digest generation details")


def test_hidden_link_count_cue_when_trimmed() -> None:
    parsed = ParsedMessage(
        message_key="k-hidden",
        content_hash=compute_content_hash("hidden body"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Hidden Links",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="hidden body",
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
            "https://example.com/post/6",
            "https://example.com/post/7",
            "https://example.com/post/8",
            "https://example.com/post/9",
            "https://example.com/post/10",
            "https://example.com/post/11",
        ),
        link_items=tuple(
            LinkItem(f"https://example.com/post/{index}", f"Article {index}", None, index)
            for index in range(1, 12)
        ),
        read_time_minutes=1,
        preview="hidden body",
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
    html = render_html(report, 8)
    assert "<details class='hidden-links'>" in html
    assert "<p class='hidden-link-cue'>" not in html
    assert "+3 more links in original" in html
    hidden_pos = html.index("<details class='hidden-links'>")
    other_pos = html.index("<details class='other-links'>")
    assert other_pos < hidden_pos
    hidden_section = html[hidden_pos : html.index("</details>", hidden_pos)]
    assert hidden_section.count("<li>") == 3


def test_no_hidden_link_cue_when_none_hidden() -> None:
    html = render_html(_report(), 8)
    assert "<p class='hidden-link-cue'>" not in html
    assert "more links in original" not in html


def test_hidden_link_cue_when_no_key_links() -> None:
    parsed = ParsedMessage(
        message_key="k-only-hidden",
        content_hash=compute_content_hash("only hidden"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Only Hidden",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="only hidden",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=("http://www.w3.org/1999/xhtml",),
        link_items=(LinkItem("http://www.w3.org/1999/xhtml", None, None, 0),),
        read_time_minutes=1,
        preview="only hidden",
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
    html = render_html(report, 8)
    assert "<strong>Key links:</strong>" not in html
    assert "<details class='hidden-links'>" in html
    assert "+1 more links in original" in html
    assert "<details class='other-links'>" not in html


def test_hidden_link_count_is_render_items_not_unique_destinations() -> None:
    href = "https://example.com/article"
    parsed = ParsedMessage(
        message_key="k-dup-hidden",
        content_hash=compute_content_hash("dup hidden"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Dup Hidden",
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text="dup hidden",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(href, href, "http://www.w3.org/1999/xhtml"),
        link_items=(
            LinkItem(href, "Article A", None, 0),
            LinkItem(href, "Article B", None, 1),
            LinkItem("http://www.w3.org/1999/xhtml", None, None, 2),
        ),
        read_time_minutes=1,
        preview="dup hidden",
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
    html = render_html(report, 8)
    assert "+2 more links in original" in html


def test_atomic_write_failure_cleans_partials(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    stem = digest_output_stem(now)
    original_rename = Path.rename

    def fail_on_html_rename(self, target):
        if str(target).endswith(".html"):
            raise OSError("simulated html rename failure")
        return original_rename(self, target)

    with patch.object(Path, "rename", fail_on_html_rename):
        with pytest.raises(OSError, match="simulated"):
            atomic_write_digest(tmp_path, now, "# Test\n", "<html></html>")

    assert not (tmp_path / f"{stem}.md").exists()
    assert not (tmp_path / f"{stem}.html").exists()
    assert not list(tmp_path.glob(".tmp-*"))


def test_render_run_details_includes_final_review_summary() -> None:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    review = FinalReviewResult(
        overall_status="pass_with_warnings",
        safe_to_publish=True,
        issues=(
            FinalReviewIssue(
                severity="minor",
                type="style_drift",
                location="tech / Test Subject",
                entry_id="k1",
                description="Mixed bullet styles",
                suggested_fix=None,
                safe_auto_fix=False,
            ),
        ),
        patches=(),
        review_source="ollama",
        profile_name="strict",
        model="qwen2.5:7b",
        prompt_version="final_review_v1",
        generated_at=now,
        digest_fingerprint="abc",
        review_input_hash="def",
    )
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (_entry(),)},
        undated=(),
        stats=DigestStats(
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
        ),
        final_review=review,
    )
    md = render_markdown(report, 8)
    html = render_html(report, 8)
    assert "### Final review" in md
    assert format_final_review_digest_summary(review) in md
    assert "<h3 class='run-details-heading'>Final review</h3>" in html
    assert "<h3 class='run-details-heading'>Stats</h3>" in html
    assert "Mixed bullet styles" in html
