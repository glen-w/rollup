"""Deterministic subreddit digest blurbs (no LLM required)."""

from __future__ import annotations

from rollup.addons.offline_text import (
    SUBREDDIT_SUBJECT_MAX,
    SUBREDDIT_SUMMARY_MAX,
    clip_heading,
    strip_urls_for_offline,
    truncate_summary,
)
from rollup.models import DigestEntry
from rollup.summarize import clean_summary_output


def deterministic_subreddit_blurb(entries: list[DigestEntry]) -> str:
    """Title list for the roundup. Every post; titles kept whole up to parse cap."""
    lines: list[str] = []
    for entry in entries:
        parsed = entry.classified.parsed
        title = parsed.subject.strip() or "(no title)"
        lines.append(f"- {clip_heading(title, SUBREDDIT_SUBJECT_MAX)}")
    return "\n".join(lines)


def reddit_entry_display(
    entry: DigestEntry, *, offline: bool = False
) -> tuple[str, str]:
    """Subject and summary for compact subreddit lists (reading surface)."""
    parsed = entry.classified.parsed
    subject = clip_heading(
        parsed.subject.strip() or "(no title)", SUBREDDIT_SUBJECT_MAX
    )
    raw = (entry.summary or parsed.preview or "").strip()
    if entry.summary:
        raw = clean_summary_output(raw)
    if offline:
        raw = strip_urls_for_offline(raw)
    summary = truncate_summary(raw, SUBREDDIT_SUMMARY_MAX)
    return subject, summary
