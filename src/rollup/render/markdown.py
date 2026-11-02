"""Markdown emitter over the render model. Formats only; decides nothing."""

from __future__ import annotations

from rollup.addons.offline_text import indent_multiline
from rollup.assets import LOGO_FILENAME
from rollup.links import render_link_markdown
from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport
from rollup.render import model as m
from rollup.render.model import CoverageIndex, FolderThemeMap


def _synthesis(report: DigestReport) -> list[str]:
    synthesis = report.briefing_synthesis
    if synthesis is None or not synthesis.bullets:
        return []
    lines = [f"## {synthesis.headline or m.GLANCE_DEFAULT_TITLE}", ""]
    lines.extend(f"- {bullet}" for bullet in synthesis.bullets)
    if synthesis.what_to_skip:
        lines.append("")
        lines.append("Safely skip:")
        lines.extend(f"- {bullet}" for bullet in synthesis.what_to_skip)
    lines.append("")
    return lines


def _start_here(report: DigestReport) -> list[str]:
    entries = m.start_here_entries(report)
    if not entries:
        return []
    lines = [f"## {m.START_HERE_TITLE}", ""]
    for entry in entries:
        p = entry.classified.parsed
        lines.append(f"- **{p.subject}** — {m.display_sender(p.sender)}")
    lines.append("")
    return lines


def _toc(report: DigestReport, folder_themes: FolderThemeMap | None) -> list[str]:
    lines = [f"## {m.CONTENTS_TITLE}", ""]
    for folder, _section_id, count in m.folder_anchor_map(report, folder_themes):
        lines.append(f"- {m.folder_display_name(folder, folder_themes)} ({count})")
    if report.undated:
        lines.append(f"- {m.UNDATED_SECTION_TITLE}")
    lines.append("")
    return lines


def _summary_metadata(report: DigestReport) -> list[str]:
    metadata = report.summary_metadata
    if metadata is None:
        return []
    if metadata.variant_name:
        lines = [
            "### Summary variant",
            "",
            f"- **Profile:** {metadata.variant_name}",
            f"- **Models used:** {', '.join(metadata.models_used) or 'none'}",
            f"- **Summaries:** {m.summaries_line(metadata)}",
            "",
        ]
    else:
        lines = [
            "### Summary routing",
            "",
            f"- **Mode:** {metadata.mode}",
            f"- **Profiles used:** {', '.join(metadata.profiles_used) or 'none'}",
            f"- **Models used:** {', '.join(metadata.models_used) or 'none'}",
            f"- **Summaries:** {m.summaries_line(metadata)}",
            "",
        ]
    if metadata.routing_counts:
        lines.extend(["```", "type | profile | model | count"])
        for row in metadata.routing_counts:
            lines.append(f"{row.newsletter_type} | {row.profile_name} | {row.model} | {row.count}")
        lines.extend(["```", ""])
    return lines


def _final_review(report: DigestReport) -> list[str]:
    text = m.final_review_text(report)
    if text is None:
        return []
    return ["### Final review", "", "```", text, "```", ""]


def _run_details(report: DigestReport) -> list[str]:
    lines = [
        f"## {m.RUN_DETAILS_TITLE}",
        "",
        "### Stats",
        "",
        "```",
        m.render_stats_block(report.stats),
        "```",
        "",
    ]
    lines.extend(_summary_metadata(report))
    lines.extend(_final_review(report))
    return lines


def render_group_markdown(group: DigestGroup, max_display_links: int) -> str:
    """Plain Markdown group structure — no HTML dependency."""
    members = m.group_member_views(group)
    lines = [f"### {m.group_title(group)}", ""]
    if group.group_type == "subreddit_digest":
        if group.group_summary:
            lines.extend([f"**{m.group_summary_label(group)}:**", "", group.group_summary.strip(), ""])
        for i, row in enumerate(members, start=1):
            lines.append(f"{i}. **{row.day}** — {row.subject}")
            if row.summary:
                lines.append("")
                lines.append(indent_multiline(row.summary))
            lines.append("")
        return "\n".join(lines)

    if group.group_summary:
        lines.extend([f"**{m.group_summary_label(group)}:**", "", group.group_summary.strip(), ""])
    else:
        lines.extend([m.group_fallback_intro(group), ""])

    if group.group_type == "notification_stream":
        for i, row in enumerate(members, start=1):
            lines.append(f"{i}. **{row.day}** — {row.subject}")
            if row.preview:
                lines.append(f"   Preview: {row.preview}")
            lines.append("")
        return "\n".join(lines)

    # daily_editions
    for row in members:
        lines.append(f"#### {row.day} — {row.subject}")
        if row.preview:
            lines.append(f"Preview: {row.preview}")
        lines.append("")
    return "\n".join(lines)


def render_entry_markdown(
    entry: DigestEntry,
    max_display_links: int,
    folder_themes: FolderThemeMap | None = None,
    coverage: CoverageIndex | None = None,
) -> str:
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type
    bundle = m.link_bundle(entry, max_display_links)
    lines = [
        f"### {p.subject}",
        "",
        f"- **From:** {p.sender} · **Date:** {m.format_date(p.date_parsed)} · "
        f"**Read:** {m.format_read_time(p.read_time_minutes)} · **Type:** {ntype}",
        f"- **Folder:** {m.folder_display_name(p.folder_name, folder_themes)}",
    ]
    label = m.attention_label(entry)
    if label:
        lines.append(f"- **Attention:** {label}")
    continuity = m.continuity_chrome(entry)
    if continuity:
        lines.append(f"- **Continuity:** {continuity}")
    note = m.coverage_note(entry, coverage)
    if note:
        lines.append(f"- **{note.label}:** {note.text}")
    lines.append("")
    summary = m.cleaned_summary(entry)
    if summary:
        lines.append(summary)
        lines.append("")
    if bundle.main_links:
        lines.append("**Key links:**")
        lines.extend(render_link_markdown(link) for link in bundle.main_links)
        lines.append("")
    if bundle.other_links:
        lines.append("**Other links:**")
        lines.extend(render_link_markdown(link) for link in bundle.other_links)
        lines.append("")
    if bundle.hidden_links:
        lines.append(f"**{m.hidden_link_cue_text(len(bundle.hidden_links))}:**")
        lines.extend(render_link_markdown(link) for link in bundle.hidden_links)
        lines.append("")
    return "\n".join(lines)


def _item(
    item: DigestItem,
    max_display_links: int,
    folder_themes: FolderThemeMap | None,
    coverage: CoverageIndex | None,
) -> str:
    if isinstance(item, DigestGroup):
        return render_group_markdown(item, max_display_links)
    return render_entry_markdown(item, max_display_links, folder_themes, coverage=coverage)


def render_markdown(
    report: DigestReport,
    max_display_links: int,
    folder_themes: FolderThemeMap | None = None,
) -> str:
    gen_date = report.generated_at.strftime("%Y-%m-%d")
    ws = report.window_start.strftime("%Y-%m-%d")
    we = report.window_end.strftime("%Y-%m-%d")
    total = report.stats.dated_included + report.stats.undated_needing_review
    lines = [
        f"![{m.ROLLUP_TITLE}]({LOGO_FILENAME})",
        "",
        f"# {m.ROLLUP_TITLE} — {gen_date}",
        "",
        f"_Week of {ws} to {we} · {total} newsletters_",
        "",
    ]
    lines.extend(_synthesis(report))
    lines.extend(_start_here(report))
    lines.extend(_toc(report, folder_themes))
    coverage = m.coverage_index(report)
    for folder in m.sort_folder_names(report.dated_by_folder.keys(), folder_themes):
        lines.append(f"## {m.folder_display_name(folder, folder_themes)}")
        lines.append("")
        for item in m.sort_digest_items(report.dated_by_folder[folder]):
            lines.append(_item(item, max_display_links, folder_themes, coverage))
    if report.undated:
        lines.append(f"## {m.UNDATED_SECTION_TITLE}")
        lines.append("")
        for item in m.sort_digest_items(report.undated):
            lines.append(_item(item, max_display_links, folder_themes, coverage))
    lines.extend(_run_details(report))
    return "\n".join(lines).rstrip() + "\n"
