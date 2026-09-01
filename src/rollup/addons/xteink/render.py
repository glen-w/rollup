"""XTEINK optimized digest rendering (Markdown, e-ink friendly)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Mapping

from rollup.addons.offline_text import (
    OFFLINE_LINE_LENGTH,
    strip_urls_for_offline,
    truncate_with_ellipsis,
    wrap_text,
)
from rollup.folder_theme import FolderThemeOverride
from rollup.fsutil import atomic_write_text
from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport
from rollup.render import (
    folder_display_name,
    format_date,
    format_read_time,
)
from rollup.summarize import clean_summary_output

logger = logging.getLogger(__name__)

FolderThemeMap = Mapping[str, FolderThemeOverride]

# XTEINK-specific constants for e-ink optimization
XTEINK_LINE_LENGTH = OFFLINE_LINE_LENGTH
XTEINK_PREVIEW_MAX = 200
XTEINK_SUBJECT_MAX = 100

# Compatibility aliases for tests / callers that imported private helpers.
_strip_urls_for_xteink = strip_urls_for_offline
_wrap_text = wrap_text
_truncate = truncate_with_ellipsis


def _render_xteink_entry_md(
    entry: DigestEntry,
    max_display_links: int,
    folder_themes: FolderThemeMap | None = None,
) -> str:
    """Render a digest entry in XTEINK-optimized Markdown format (no URLs)."""
    del max_display_links  # XTEINK digests omit link lists
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type

    lines = [
        f"## {p.subject}",
        "",
        f"- From: {p.sender}",
        f"- Date: {format_date(p.date_parsed)}",
        f"- Read time: {format_read_time(p.read_time_minutes)}",
        f"- Type: {ntype}",
        f"- Folder: {folder_display_name(p.folder_name, folder_themes)}",
        "",
    ]

    if entry.summary:
        summary = _strip_urls_for_xteink(clean_summary_output(entry.summary))
        if summary:
            lines.append("### Summary")
            lines.append("")
            wrapped_summary = _wrap_text(summary, XTEINK_LINE_LENGTH)
            lines.append(wrapped_summary)
            lines.append("")

    return "\n".join(lines)


def _render_xteink_group_md(group: DigestGroup, max_display_links: int) -> str:
    """Render a digest group in XTEINK-optimized Markdown format (no URLs)."""
    del max_display_links  # XTEINK digests omit link lists
    n = len(group.entries)
    if group.group_type == "subreddit_digest":
        lines = [f"## {group.display_name} — {n} posts this week", ""]
        if group.group_summary:
            summary = _strip_urls_for_xteink(group.group_summary.strip())
            lines.extend(["**Roundup:**", "", _wrap_text(summary, XTEINK_LINE_LENGTH), ""])
        for i, entry in enumerate(group.entries, start=1):
            p = entry.classified.parsed
            subject = _truncate(p.subject, XTEINK_SUBJECT_MAX)
            date = p.date_parsed.strftime("%Y-%m-%d") if p.date_parsed else "undated"
            preview = _truncate(
                _strip_urls_for_xteink(entry.summary or p.preview or ""),
                XTEINK_PREVIEW_MAX,
            )
            lines.append(f"{i}. {date} — {subject}")
            if preview:
                lines.append(f"   Preview: {preview}")
            lines.append("")
        return "\n".join(lines)
    if group.group_type == "notification_stream":
        lines = [
            f"## {group.display_name} — {n} updates this week",
            "",
        ]
        if group.group_summary:
            summary = _strip_urls_for_xteink(group.group_summary.strip())
            lines.extend(
                [
                    "**This week:**",
                    "",
                    _wrap_text(summary, XTEINK_LINE_LENGTH),
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"Grouped notification stream ({n} messages). Newest first.",
                    "",
                ]
            )
        for i, entry in enumerate(group.entries, start=1):
            p = entry.classified.parsed
            subject = _truncate(p.subject, XTEINK_SUBJECT_MAX)
            date = (
                p.date_parsed.strftime("%Y-%m-%d") if p.date_parsed else "undated"
            )
            preview = _truncate(
                _strip_urls_for_xteink(p.preview if p.preview else ""),
                XTEINK_PREVIEW_MAX,
            )
            lines.append(f"{i}. **{date}** — {subject}")
            if preview:
                lines.append(f"   Preview: {preview}")
            lines.append("")
        return "\n".join(lines)

    # daily_editions / sender_batch
    unit = "editions" if group.group_type == "daily_editions" else "messages"
    lines = [
        f"## {group.display_name} — {n} {unit} this week",
        "",
    ]
    if group.group_summary:
        summary = _strip_urls_for_xteink(group.group_summary.strip())
        lines.extend(
            [
                "**Edition roundup:**",
                "",
                _wrap_text(summary, XTEINK_LINE_LENGTH),
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"Grouped {group.group_type.replace('_', ' ')} ({n} messages).",
                "",
            ]
        )
    for entry in group.entries:
        p = entry.classified.parsed
        subject = _truncate(p.subject, XTEINK_SUBJECT_MAX)
        date = p.date_parsed.strftime("%Y-%m-%d") if p.date_parsed else "undated"
        preview = _truncate(
            _strip_urls_for_xteink(p.preview if p.preview else ""),
            XTEINK_PREVIEW_MAX,
        )
        lines.append(f"### {date} — {subject}")
        if preview:
            lines.append(f"Preview: {preview}")
        lines.append("")
    return "\n".join(lines)


def _render_xteink_item_md(
    item: DigestItem,
    max_display_links: int,
    folder_themes: FolderThemeMap | None = None,
) -> str:
    """Render a digest item (entry or group) in XTEINK-optimized Markdown."""
    if isinstance(item, DigestGroup):
        return _render_xteink_group_md(item, max_display_links)
    return _render_xteink_entry_md(item, max_display_links, folder_themes)


def render_xteink_markdown(
    report: DigestReport,
    max_display_links: int,
    folder_themes: FolderThemeMap | None = None,
) -> str:
    """Render digest in XTEINK-optimized Markdown format."""
    gen_date = report.generated_at.strftime("%Y-%m-%d")
    ws = report.window_start.strftime("%Y-%m-%d")
    we = report.window_end.strftime("%Y-%m-%d")
    total = report.stats.dated_included + report.stats.undated_needing_review

    lines = [
        "# Rollup Digest",
        "",
        f"Generated: {gen_date}",
        f"Period: {ws} to {we}",
        f"Total newsletters: {total}",
        "",
        "## Contents",
        "",
    ]

    for folder, entries in sorted(report.dated_by_folder.items()):
        lines.append(
            f"- {folder_display_name(folder, folder_themes)} ({len(entries)})"
        )

    if report.undated:
        lines.append("- Undated / needs review")

    lines.append("")

    for folder, entries in sorted(report.dated_by_folder.items()):
        lines.append(f"## {folder_display_name(folder, folder_themes)}")
        lines.append("")
        for item in entries:
            lines.append(
                _render_xteink_item_md(item, max_display_links, folder_themes)
            )

    if report.undated:
        lines.append("## Undated / needs review")
        lines.append("")
        for item in report.undated:
            lines.append(
                _render_xteink_item_md(item, max_display_links, folder_themes)
            )

    lines.append("## Digest Generation Details")
    lines.append("")
    lines.append("**Stats:**")
    lines.append(f"- Folders scanned: {report.stats.folders_scanned}")
    lines.append(f"- Messages parsed: {report.stats.messages_parsed}")
    lines.append(f"- Dated included: {report.stats.dated_included}")
    lines.append(
        f"- Undated needing review: {report.stats.undated_needing_review}"
    )

    if report.summary_metadata:
        lines.append("")
        lines.append("**Summary Metadata:**")
        lines.append(f"- Mode: {report.summary_metadata.mode}")
        lines.append(
            f"- Profiles used: "
            f"{', '.join(report.summary_metadata.profiles_used) or 'none'}"
        )
        lines.append(
            f"- Models used: "
            f"{', '.join(report.summary_metadata.models_used) or 'none'}"
        )

    return "\n".join(lines).rstrip() + "\n"


def atomic_write_xteink_digest(
    output_dir: Path,
    generated_at: datetime,
    markdown: str,
    *,
    run_id_short: str | None = None,
) -> Path:
    """Write XTEINK-optimized Markdown atomically via temp file + rename.

    Filename uses the normal digest stem with a ``.xteink`` variant suffix, e.g.
    ``2026-07-02T103000Z-newsletter-digest.xteink.md``.
    """
    from rollup.render import digest_output_stem

    output_dir.mkdir(parents=True, exist_ok=True)

    stem = digest_output_stem(
        generated_at, variant_name="xteink", run_id_short=run_id_short
    )
    final_md = output_dir / f"{stem}.md"

    if final_md.exists():
        raise FileExistsError(
            f"XTEINK digest output already exists: {final_md.name} — "
            "refusing to overwrite"
        )

    return atomic_write_text(final_md, markdown)
