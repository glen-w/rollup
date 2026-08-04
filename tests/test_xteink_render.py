"""Tests for XTEINK render module."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from rollup.addons.xteink.render import (
    _strip_urls_for_xteink,
    _truncate,
    _wrap_text,
    atomic_write_xteink_digest,
    render_xteink_markdown,
)
from rollup.classify import classify_message
from rollup.config import compute_date_window
from rollup.filter import make_digest_entry
from rollup.folder_theme import FolderThemeOverride
from rollup.models import DigestGroup, DigestReport, DigestStats, LinkItem, ParsedMessage
from rollup.parse import compute_content_hash


def _entry(
    subject: str = "Test Subject",
    key: str = "k1",
    *,
    summary: str | None = None,
    links: tuple[str, ...] = (),
    preview: str | None = None,
):
    body = preview if preview is not None else f"Body for {subject}"
    link_items = tuple(
        LinkItem(href=href, text=None, context=None, source_index=i)
        for i, href in enumerate(links)
    )
    parsed = ParsedMessage(
        message_key=key,
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject=subject,
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=len(links),
        html_section_break_count=0,
        links=links,
        link_items=link_items,
        read_time_minutes=1,
        preview=body,
        parse_warnings=(),
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    if summary is not None:
        entry = replace(entry, summary=summary, summary_source="ollama")
    return entry


def _report_with_group() -> DigestReport:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    group = DigestGroup(
        group_id="g1",
        group_type="notification_stream",
        display_name="Cursor updates",
        sender_normalized="a@example.com",
        folder_name="tech",
        entries=(_entry("Update one", "k1"), _entry("Update two", "k2")),
        group_summary="Two product updates this week.",
    )
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (group,)},
        undated=(),
        stats=DigestStats(
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
        ),
    )


def test_xteink_render_markdown():
    assert render_xteink_markdown is not None
    assert callable(render_xteink_markdown)


def test_xteink_markdown_renders_digest_groups() -> None:
    """Regression: DigestGroup items must not be treated as DigestEntry."""
    report = _report_with_group()
    md = render_xteink_markdown(report, 8)
    assert "Cursor updates" in md
    assert "Update one" in md
    assert "Update two" in md
    assert "Two product updates this week." in md


def test_strip_urls_for_xteink() -> None:
    assert (
        _strip_urls_for_xteink("See [docs](https://example.com/docs) today")
        == "See docs today"
    )
    assert "http" not in _strip_urls_for_xteink(
        "Visit https://example.com/path now"
    )
    assert "www." not in _strip_urls_for_xteink("Visit www.example.com now")


def test_xteink_digest_omits_urls() -> None:
    """XTEINK MD must not emit link sections or http(s) destinations."""
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    entry = _entry(
        "Linked newsletter",
        "k-links",
        summary="Read [the post](https://example.com/post) and more.",
        links=("https://example.com/a", "https://example.com/b"),
        preview="Preview with https://example.com/preview inside",
    )
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
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
    )
    md = render_xteink_markdown(report, 8)
    assert "Key Links" not in md
    assert "Other Links" not in md
    assert "https://" not in md
    assert "http://" not in md
    assert "the post" in md


def test_xteink_line_wrapping():
    long_text = (
        "This is a very long piece of text that should be wrapped to fit "
        "within the specified line length for optimal readability on e-ink "
        "displays. It contains multiple words that will be broken up "
        "appropriately."
    )
    wrapped = _wrap_text(long_text, 60)
    assert wrapped is not None
    for line in wrapped.split("\n"):
        if line.strip():
            assert len(line) <= 70, f"Line too long: {line}"


def test_xteink_wrapping_edge_cases():
    assert _wrap_text("", 60) == ""
    short = "Short line"
    assert _wrap_text(short, 60) == short
    exact = "x" * 60
    result = _wrap_text(exact, 60)
    assert len(result.split("\n")[0]) <= 60


def test_xteink_output_stem(tmp_path):
    """XTEINK filenames use a .xteink variant suffix."""
    now = datetime(2026, 7, 2, 10, 30, 0, tzinfo=timezone.utc)
    md_path = atomic_write_xteink_digest(tmp_path, now, "# hi\n")
    assert md_path.name.endswith("-newsletter-digest.xteink.md")
    assert not md_path.name.startswith("xteink-")
    assert md_path.read_text(encoding="utf-8") == "# hi\n"


def test_xteink_markdown_applies_folder_themes() -> None:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    entry = _entry("Themed subject", "k-theme", summary="A short summary.")
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
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
    )
    themes = {"tech": FolderThemeOverride(emoji="💻", accent="#4a7fd4")}
    md = render_xteink_markdown(report, 8, themes)
    assert "💻 tech" in md


def test_xteink_group_preview_truncates_at_word_boundary() -> None:
    text = "word " * 50
    truncated = _truncate(text, 40)
    assert truncated.endswith("…")
    assert truncated[:-1].rstrip().split()[-1] == "word"
