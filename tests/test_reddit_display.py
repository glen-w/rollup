"""Reddit compact-list display: complete summaries across writers."""

from __future__ import annotations

import html as html_module
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.addons.offline_text import (
    SUBREDDIT_SUMMARY_MAX,
    indent_multiline,
    truncate_summary,
)
from rollup.addons.txt.render import render_txt
from rollup.addons.xteink.render import render_xteink_markdown
from rollup.classify import classify_message
from rollup.config import compute_date_window
from rollup.filter import make_digest_entry
from rollup.models import DigestGroup, DigestReport, DigestStats, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.reddit.summary import deterministic_subreddit_blurb, reddit_entry_display
from rollup.render import render_html, render_markdown

LONG_TITLE = (
    "Reads your Claude Code/ChatGPT sessions and drafts the day's LinkedIn "
    "post, which you approve in Telegram before anything publishes."
)
THREE_BULLETS = (
    "- Created termtext, a self-hostable terminal chat application using Go "
    "for the WebSocket server and Bubble Tea for the TUI client.\n"
    "- No third-party chat SDKs were used in the development.\n"
    "- Repository available at github.com/example/termText; feedback is welcome."
)


def _entry(
    *,
    key: str,
    subject: str,
    summary: str | None = None,
    date: datetime | None = None,
):
    when = date or datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    parsed = ParsedMessage(
        message_key=key,
        content_hash=compute_content_hash(subject),
        folder_name="reddit:feed",
        relative_folder_path="reddit:feed",
        subject=subject,
        sender="u/example",
        date_raw=when.isoformat(),
        date_parsed=when,
        body_text=subject,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="",
        parse_warnings=(),
        source_key="reddit:sub:coolgithubprojects",
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    if summary is not None:
        entry = replace(entry, summary=summary, summary_source="ollama")
    return entry


def _reddit_group(*, n_extra: int = 8) -> DigestGroup:
    extras = [
        _entry(key=f"extra-{i}", subject=f"Short post {i}")
        for i in range(n_extra)
    ]
    featured = _entry(
        key="featured",
        subject=LONG_TITLE,
        summary=THREE_BULLETS,
    )
    entries = tuple(extras + [featured])
    return DigestGroup(
        group_id="g-reddit",
        group_type="subreddit_digest",
        display_name="r/coolgithubprojects",
        sender_normalized="r/coolgithubprojects",
        folder_name="reddit:feed",
        entries=entries,
        group_summary=deterministic_subreddit_blurb(list(entries)),
        group_summary_source="preview_fallback",
        render_mode="expandable",
    )


def _report(group: DigestGroup) -> DigestReport:
    now = datetime(2026, 9, 1, 15, 41, tzinfo=timezone.utc)
    start, end = compute_date_window(now, 7)
    n = len(group.entries)
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"reddit:feed": (group,)},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=n,
            dated_included=n,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=1,
            summaries_cache=0,
            summaries_fallback=n - 1,
        ),
    )


def test_truncate_summary_prefers_complete_bullets() -> None:
    overflow = (
        "- keep me as a reasonably complete first bullet about the device\n"
        "- also keep this second bullet about firmware and setup\n"
        "- " + ("word " * 80)
    )
    clipped = truncate_summary(overflow, 200)
    assert clipped.endswith("…")
    assert "keep me" in clipped
    assert "also keep" in clipped
    assert "word word" not in clipped


def test_truncate_summary_under_cap_is_unchanged() -> None:
    assert truncate_summary(THREE_BULLETS, SUBREDDIT_SUMMARY_MAX) == THREE_BULLETS
    assert len(THREE_BULLETS) > 200


def test_indent_multiline_nests_under_list_item() -> None:
    assert indent_multiline("- a\n- b") == "   - a\n   - b"


def test_roundup_lists_every_title_without_ellipsis() -> None:
    group = _reddit_group(n_extra=8)
    blurb = group.group_summary or ""
    assert LONG_TITLE in blurb
    assert "…and" not in blurb
    for i in range(8):
        assert f"Short post {i}" in blurb
    assert blurb.count("\n") == 8


def test_reddit_entry_display_keeps_three_bullets() -> None:
    entry = _entry(key="k", subject=LONG_TITLE, summary=THREE_BULLETS)
    subject, summary = reddit_entry_display(entry)
    assert subject == LONG_TITLE
    assert "…" not in summary
    assert "Bubble Tea" in summary
    assert "No third-party chat SDKs" in summary
    assert "feedback is welcome" in summary


def test_markdown_and_html_keep_complete_reddit_summaries() -> None:
    report = _report(_reddit_group())
    md = render_markdown(report, 8)
    assert "Preview:" not in md
    assert LONG_TITLE in md
    assert "Bubble Tea" in md
    assert "feedback is welcome" in md
    assert "…and" not in md

    html = render_html(report, 8)
    assert html_module.escape(LONG_TITLE) in html
    assert "<ul>" in html
    assert "Bubble Tea" in html
    assert "feedback is welcome" in html
    assert "Preview:" not in html


def test_txt_and_xteink_keep_complete_reddit_summaries() -> None:
    report = _report(_reddit_group())
    txt = render_txt(report, 8)
    xteink = render_xteink_markdown(report, 8)
    for text in (txt, xteink):
        assert LONG_TITLE in text
        assert "Bubble Tea" in text
        assert "feedback is welcome" in text
        assert "Preview:" not in text
        assert "…and" not in text


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("ebooklib") is None,
    reason="ebooklib not installed",
)
def test_epub_keeps_complete_reddit_summaries(tmp_path: Path) -> None:
    from rollup.addons.epub.render import render_epub_bytes

    report = _report(_reddit_group())
    data = render_epub_bytes(report, 8)
    path = tmp_path / "digest.epub"
    path.write_bytes(data)
    with zipfile.ZipFile(path) as zf:
        folder_files = [
            n for n in zf.namelist() if "folder-" in n and n.endswith(".xhtml")
        ]
        assert folder_files
        content = "\n".join(zf.read(n).decode("utf-8") for n in folder_files)
    assert "Claude Code/ChatGPT" in content
    assert "Telegram before anything publishes" in content
    assert "Bubble Tea" in content
    assert "feedback is welcome" in content
    assert "<ul>" in content
    assert "Preview:" not in content
