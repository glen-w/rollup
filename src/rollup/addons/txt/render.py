"""Plain-text digest rendering (no links)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rollup.addons.artifact_write import atomic_write_digest_artifact
from rollup.addons.offline_text import (
    OFFLINE_LINE_LENGTH,
    strip_urls_for_offline,
    wrap_text,
)
from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport
from rollup.render import folder_display_name, format_date, format_read_time
from rollup.summarize import clean_summary_output


def _render_entry_txt(entry: DigestEntry) -> str:
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type
    lines = [
        p.subject,
        "-" * min(len(p.subject), OFFLINE_LINE_LENGTH),
        f"From: {p.sender}",
        f"Date: {format_date(p.date_parsed)}",
        f"Read time: {format_read_time(p.read_time_minutes)}",
        f"Type: {ntype}",
        f"Folder: {folder_display_name(p.folder_name)}",
        "",
    ]
    if entry.summary:
        summary = strip_urls_for_offline(clean_summary_output(entry.summary))
        if summary:
            lines.append("Summary")
            lines.append("")
            lines.append(wrap_text(summary, OFFLINE_LINE_LENGTH))
            lines.append("")
    return "\n".join(lines)


def _render_group_txt(group: DigestGroup) -> str:
    n = len(group.entries)
    if group.group_type == "notification_stream":
        lines = [
            f"{group.display_name} — {n} updates this week",
            "",
        ]
        if group.group_summary:
            summary = strip_urls_for_offline(group.group_summary.strip())
            lines.extend(
                [
                    "This week:",
                    "",
                    wrap_text(summary, OFFLINE_LINE_LENGTH),
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
            subject = p.subject[:100]
            date = (
                p.date_parsed.strftime("%Y-%m-%d") if p.date_parsed else "undated"
            )
            preview = strip_urls_for_offline(p.preview[:200] if p.preview else "")
            lines.append(f"{i}. {date} — {subject}")
            if preview:
                lines.append(f"   Preview: {preview}")
            lines.append("")
        return "\n".join(lines)

    unit = "editions" if group.group_type == "daily_editions" else "messages"
    lines = [
        f"{group.display_name} — {n} {unit} this week",
        "",
    ]
    if group.group_summary:
        summary = strip_urls_for_offline(group.group_summary.strip())
        lines.extend(
            [
                "Edition roundup:",
                "",
                wrap_text(summary, OFFLINE_LINE_LENGTH),
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
        subject = p.subject[:100]
        date = p.date_parsed.strftime("%Y-%m-%d") if p.date_parsed else "undated"
        preview = strip_urls_for_offline(p.preview[:200] if p.preview else "")
        lines.append(f"{date} — {subject}")
        if preview:
            lines.append(f"Preview: {preview}")
        lines.append("")
    return "\n".join(lines)


def _render_item_txt(item: DigestItem) -> str:
    if isinstance(item, DigestGroup):
        return _render_group_txt(item)
    return _render_entry_txt(item)


def render_txt(report: DigestReport, max_display_links: int) -> str:
    """Render digest as link-free plain text."""
    del max_display_links  # TXT digests omit link lists
    gen_date = report.generated_at.strftime("%Y-%m-%d")
    ws = report.window_start.strftime("%Y-%m-%d")
    we = report.window_end.strftime("%Y-%m-%d")
    total = report.stats.dated_included + report.stats.undated_needing_review

    lines = [
        "Rollup Digest",
        "=============",
        "",
        f"Generated: {gen_date}",
        f"Period: {ws} to {we}",
        f"Total newsletters: {total}",
        "",
        "Contents",
        "--------",
        "",
    ]

    for folder, entries in sorted(report.dated_by_folder.items()):
        lines.append(f"- {folder_display_name(folder)} ({len(entries)})")

    if report.undated:
        lines.append("- Undated / needs review")

    lines.append("")

    for folder, entries in sorted(report.dated_by_folder.items()):
        title = folder_display_name(folder)
        lines.append(title)
        lines.append("=" * min(len(title), OFFLINE_LINE_LENGTH))
        lines.append("")
        for item in entries:
            lines.append(_render_item_txt(item))

    if report.undated:
        lines.append("Undated / needs review")
        lines.append("======================")
        lines.append("")
        for item in report.undated:
            lines.append(_render_item_txt(item))

    lines.append("Digest Generation Details")
    lines.append("-------------------------")
    lines.append("")
    lines.append("Stats:")
    lines.append(f"- Folders scanned: {report.stats.folders_scanned}")
    lines.append(f"- Messages parsed: {report.stats.messages_parsed}")
    lines.append(f"- Dated included: {report.stats.dated_included}")
    lines.append(
        f"- Undated needing review: {report.stats.undated_needing_review}"
    )

    if report.summary_metadata:
        lines.append("")
        lines.append("Summary Metadata:")
        lines.append(f"- Mode: {report.summary_metadata.mode}")
        lines.append(
            "- Profiles used: "
            f"{', '.join(report.summary_metadata.profiles_used) or 'none'}"
        )
        lines.append(
            "- Models used: "
            f"{', '.join(report.summary_metadata.models_used) or 'none'}"
        )

    return "\n".join(lines).rstrip() + "\n"


def atomic_write_txt_digest(
    output_dir: Path,
    generated_at: datetime,
    text: str,
    *,
    run_id_short: str | None = None,
) -> Path:
    """Write plain-text digest beside the core stem (``.txt``)."""
    return atomic_write_digest_artifact(
        output_dir,
        generated_at,
        text,
        extension="txt",
        run_id_short=run_id_short,
    )
