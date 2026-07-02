"""Markdown and HTML digest rendering with atomic writes."""

from __future__ import annotations

import html as html_module
import logging
import re
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

from rollup.assets import FAVICON_FILENAME, LOGO_FILENAME
from rollup.links import (
    prepare_links_for_render,
    render_link_html,
    render_link_markdown,
)
from rollup.models import LinkItem
from rollup.models import DigestEntry, DigestReport, DigestStats
from rollup.final_review import format_final_review_digest_summary

logger = logging.getLogger(__name__)

ROLLUP_TITLE = "Rollup"

_FOLDERS_SLUG_RE = re.compile(r"[^a-z0-9]+")

FOLDER_EMOJI: dict[str, str] = {
    "brainfood": "🧠",
    "enviro": "🌲",
    "hoops": "🏀",
    "tech": "💻",
    "misc": "📬",
    "trackerwall": "📰",
}

FOLDER_ACCENT: dict[str, str] = {
    "brainfood": "#e8a0bf",
    "enviro": "#4a9e6b",
    "hoops": "#e8923a",
    "tech": "#4a7fd4",
    "misc": "#8b7fa8",
    "trackerwall": "#9a8b7a",
}

DEFAULT_FOLDER_ACCENT = "#ccc"


def _format_date(dt: datetime | None) -> str:
    if dt is None:
        return "undated"
    return dt.strftime("%Y-%m-%d %H:%M")


def _format_window_range(start: datetime, end: datetime) -> str:
    """Human-readable digest window, e.g. 23-30 July 2026 or 30 July - 6 August 2026."""
    start_day = start.day
    end_day = end.day
    if start.year == end.year and start.month == end.month:
        return f"{start_day}-{end_day} {end.strftime('%B %Y')}"
    if start.year == end.year:
        return (
            f"{start_day} {start.strftime('%B')} - "
            f"{end_day} {end.strftime('%B %Y')}"
        )
    return (
        f"{start_day} {start.strftime('%B %Y')} - "
        f"{end_day} {end.strftime('%B %Y')}"
    )


def _display_sender(sender: str) -> str:
    """Return a human-readable sender name without the email address."""
    name, addr = parseaddr(sender)
    display = name.strip()
    if display:
        return display
    if addr:
        return addr.split("@", 1)[0]
    return sender.strip() or "(unknown)"


def _folder_display_name(folder: str) -> str:
    emoji = FOLDER_EMOJI.get(folder.lower())
    if emoji:
        return f"{emoji} {folder}"
    return folder


def _folder_accent_class(folder: str) -> str:
    slug = _folder_slug(folder)
    if slug in FOLDER_ACCENT:
        return f"folder-accent-{slug}"
    return "folder-accent-default"


def _folder_accent_color(folder: str) -> str:
    return FOLDER_ACCENT.get(_folder_slug(folder), DEFAULT_FOLDER_ACCENT)


def _format_newsletter_type(ntype: str) -> str:
    parts = ntype.split("_")
    if len(parts) == 1:
        return parts[0].capitalize()
    return f"{parts[0].capitalize()} {' '.join(parts[1:])}"


def _sort_entries_by_read_time(
    entries: tuple[DigestEntry, ...],
) -> tuple[DigestEntry, ...]:
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                entry.classified.parsed.read_time_minutes,
                entry.classified.parsed.subject.lower(),
            ),
        )
    )


def _folder_accent_css() -> str:
    rules: list[str] = []
    for slug, color in FOLDER_ACCENT.items():
        selector = f".folder-accent-{slug}"
        rules.append(f"{selector}>h2{{border-left:4px solid {color};padding-left:0.5rem;}}")
        rules.append(
            f"{selector} .newsletter-card{{border-color:{color};border-left-width:3px;}}"
        )
    rules.append(
        f".folder-accent-default>h2{{border-left:4px solid {DEFAULT_FOLDER_ACCENT};padding-left:0.5rem;}}"
    )
    rules.append(
        f".folder-accent-default .newsletter-card{{border-color:{DEFAULT_FOLDER_ACCENT};border-left-width:3px;}}"
    )
    return "".join(rules)


def _folder_slug(folder: str) -> str:
    slug = _FOLDERS_SLUG_RE.sub("-", folder.lower()).strip("-")
    return slug or "folder"


def _folder_section_id(folder: str) -> str:
    return f"folder-{_folder_slug(folder)}"


def _folder_anchor_map(
    report: DigestReport,
) -> list[tuple[str, str, int]]:
    slug_counts: dict[str, int] = {}
    anchors: list[tuple[str, str, int]] = []
    for folder, entries in sorted(report.dated_by_folder.items()):
        base = _folder_slug(folder)
        slug_counts[base] = slug_counts.get(base, 0) + 1
        count = slug_counts[base]
        section_id = _folder_section_id(folder) if count == 1 else f"folder-{base}-{count}"
        anchors.append((folder, section_id, len(entries)))
    return anchors


def _format_read_time(minutes: int) -> str:
    return f"🕐 {minutes} min"


def _format_section_byline(entries: tuple[DigestEntry, ...]) -> str:
    count = len(entries)
    total_minutes = sum(
        entry.classified.parsed.read_time_minutes for entry in entries
    )
    newsletter_word = "newsletter" if count == 1 else "newsletters"
    minute_word = "minute" if total_minutes == 1 else "minutes"
    return (
        f"{count} {newsletter_word}, "
        f"{total_minutes} {minute_word} reading time"
    )


def _render_section_byline_html(entries: tuple[DigestEntry, ...]) -> str:
    return (
        "<p class='folder-byline'>"
        f"<em>{html_module.escape(_format_section_byline(entries))}</em>"
        "</p>"
    )


_INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _inline_summary_markdown(text: str) -> str:
    return _INLINE_BOLD_RE.sub(r"<strong>\1</strong>", text)


def _render_summary_html(text: str) -> str:
    """Render a small subset of markdown used in LLM summaries."""
    lines = text.splitlines()
    parts: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("### "):
            heading = _inline_summary_markdown(html_module.escape(stripped[4:]))
            parts.append(f"<h3>{heading}</h3>")
        elif stripped.startswith("## "):
            heading = _inline_summary_markdown(html_module.escape(stripped[3:]))
            parts.append(f"<h3>{heading}</h3>")
        elif stripped.startswith("# "):
            heading = _inline_summary_markdown(html_module.escape(stripped[2:]))
            parts.append(f"<h3>{heading}</h3>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            items: list[str] = []
            while i < len(lines):
                item = lines[i].strip()
                if item.startswith("- ") or item.startswith("* "):
                    item_text = _inline_summary_markdown(html_module.escape(item[2:]))
                    items.append(f"<li>{item_text}</li>")
                    i += 1
                else:
                    break
            parts.append("<ul>" + "".join(items) + "</ul>")
            continue
        else:
            para_lines = [stripped]
            i += 1
            while i < len(lines):
                item = lines[i].strip()
                if (
                    not item
                    or item.startswith("#")
                    or item.startswith("- ")
                    or item.startswith("* ")
                ):
                    break
                para_lines.append(item)
                i += 1
            paragraph = _inline_summary_markdown(
                html_module.escape(" ".join(para_lines))
            )
            parts.append(f"<p>{paragraph}</p>")
            continue
        i += 1
    return "".join(parts)


def render_stats_block(stats: DigestStats) -> str:
    return (
        f"Folders scanned: {stats.folders_scanned}\n"
        f"Messages parsed: {stats.messages_parsed}\n"
        f"Dated included: {stats.dated_included}\n"
        f"Undated needing review: {stats.undated_needing_review}\n"
        f"Skipped outside window: {stats.skipped_outside_window}\n"
        f"Skipped seen undated: {stats.skipped_seen_undated}\n"
        f"Deduped messages: {stats.deduped_messages}\n"
        f"Parse errors: {stats.parse_errors}\n"
        f"Summaries: Ollama {stats.summaries_ollama} · "
        f"cache {stats.summaries_cache} · fallback {stats.summaries_fallback} · "
        f"errors {stats.summaries_errors}"
    )


def _render_summary_metadata_md(report: DigestReport) -> list[str]:
    metadata = report.summary_metadata
    if metadata is None:
        return []
    if metadata.variant_name:
        lines = [
            "### Summary variant",
            "",
            f"- **Profile:** {metadata.variant_name}",
            f"- **Models used:** {', '.join(metadata.models_used) or 'none'}",
            f"- **Summaries:** Ollama {metadata.summaries_ollama} · cache {metadata.summaries_cache} · fallback {metadata.summaries_fallback} · errors {metadata.summaries_errors}",
            "",
        ]
    else:
        lines = [
            "### Summary routing",
            "",
            f"- **Mode:** {metadata.mode}",
            f"- **Profiles used:** {', '.join(metadata.profiles_used) or 'none'}",
            f"- **Models used:** {', '.join(metadata.models_used) or 'none'}",
            f"- **Summaries:** Ollama {metadata.summaries_ollama} · cache {metadata.summaries_cache} · fallback {metadata.summaries_fallback} · errors {metadata.summaries_errors}",
            "",
        ]
    if metadata.routing_counts:
        lines.extend(["```", "type | profile | model | count"])
        for row in metadata.routing_counts:
            lines.append(
                f"{row.newsletter_type} | {row.profile_name} | {row.model} | {row.count}"
            )
        lines.extend(["```", ""])
    return lines


def _render_summary_metadata_html(report: DigestReport) -> str:
    metadata = report.summary_metadata
    if metadata is None:
        return ""
    title = "Summary variant" if metadata.variant_name else "Summary routing"
    items = []
    if metadata.variant_name:
        items.append(
            f"<li><strong>Profile:</strong> {html_module.escape(metadata.variant_name)}</li>"
        )
    else:
        items.append(
            f"<li><strong>Mode:</strong> {html_module.escape(metadata.mode)}</li>"
        )
        items.append(
            f"<li><strong>Profiles used:</strong> {html_module.escape(', '.join(metadata.profiles_used) or 'none')}</li>"
        )
    items.append(
        f"<li><strong>Models used:</strong> {html_module.escape(', '.join(metadata.models_used) or 'none')}</li>"
    )
    items.append(
        "<li><strong>Summaries:</strong> "
        f"Ollama {metadata.summaries_ollama} · cache {metadata.summaries_cache} · "
        f"fallback {metadata.summaries_fallback} · errors {metadata.summaries_errors}</li>"
    )
    table = ""
    if metadata.routing_counts:
        rows = "".join(
            "<tr>"
            f"<td>{html_module.escape(row.newsletter_type)}</td>"
            f"<td>{html_module.escape(row.profile_name)}</td>"
            f"<td>{html_module.escape(row.model)}</td>"
            f"<td>{row.count}</td>"
            "</tr>"
            for row in metadata.routing_counts
        )
        table = (
            "<table><thead><tr><th>Type</th><th>Profile</th><th>Model</th><th>Count</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    return (
        f"<section class='run-details-section summary-metadata'>"
        f"<h3 class='run-details-heading'>{html_module.escape(title)}</h3>"
        f"<ul>{''.join(items)}</ul>{table}</section>"
    )


def _render_final_review_md(report: DigestReport) -> list[str]:
    result = report.final_review
    if result is None:
        return []
    return [
        "### Final review",
        "",
        "```",
        format_final_review_digest_summary(result),
        "```",
        "",
    ]


def _render_final_review_html(report: DigestReport) -> str:
    result = report.final_review
    if result is None:
        return ""
    summary = html_module.escape(format_final_review_digest_summary(result))
    return (
        "<section class='run-details-section final-review'>"
        "<h3 class='run-details-heading'>Final review</h3>"
        f"<div class='stats'>{summary}</div>"
        "</section>"
    )


def _render_run_details_html(report: DigestReport) -> str:
    metadata_html = _render_summary_metadata_html(report)
    final_review_html = _render_final_review_html(report)
    return (
        "<details class='run-details'>"
        "<summary>Digest generation details</summary>"
        "<section class='run-details-section stats-section'>"
        "<h3 class='run-details-heading'>Stats</h3>"
        f"<div class='stats'>{html_module.escape(render_stats_block(report.stats))}</div>"
        "</section>"
        f"{metadata_html}"
        f"{final_review_html}"
        "</details>"
    )


def _render_toc_html(
    anchor_map: list[tuple[str, str, int]], *, include_undated: bool
) -> str:
    items = [
        "<li>"
        f"<a href='#{html_module.escape(section_id)}'>"
        f"{html_module.escape(_folder_display_name(folder))} ({count})"
        "</a></li>"
        for folder, section_id, count in anchor_map
    ]
    if include_undated:
        items.append(
            "<li><a href='#undated'>Undated / needs review</a></li>"
        )
    controls = (
        "<p class='rollup-controls'>"
        "<button type='button' id='expand-all-cards' "
        "aria-label='Expand all newsletter cards'>Expand all</button>"
        " · "
        "<button type='button' id='collapse-all-cards' "
        "aria-label='Collapse all newsletter cards'>Collapse all</button>"
        "</p>"
    )
    return (
        f"<nav class='rollup-toc' aria-label='Contents'>{controls}"
        f"<ul>{''.join(items)}</ul></nav>"
    )


def _render_toc_md(report: DigestReport) -> list[str]:
    lines = ["## Contents", ""]
    for folder, _section_id, count in _folder_anchor_map(report):
        lines.append(f"- {_folder_display_name(folder)} ({count})")
    if report.undated:
        lines.append("- Undated / needs review")
    lines.append("")
    return lines


def _render_run_details_md(report: DigestReport) -> list[str]:
    lines = [
        "## Digest generation details",
        "",
        "### Stats",
        "",
        "```",
        render_stats_block(report.stats),
        "```",
        "",
    ]
    metadata_lines = _render_summary_metadata_md(report)
    if metadata_lines:
        lines.extend(metadata_lines)
    lines.extend(_render_final_review_md(report))
    return lines


def _hidden_link_cue_text(hidden_count: int) -> str:
    return f"+{hidden_count} more links in original"


def _render_entry_md(entry: DigestEntry, max_display_links: int) -> str:
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type
    link_items = (
        list(p.link_items)
        if getattr(p, "link_items", ())
        else [
            LinkItem(href=href, text=None, context=None, source_index=index)
            for index, href in enumerate(p.links)
        ]
    )
    max_main = min(5, max_display_links)
    max_other = max(0, max_display_links - max_main)
    bundle = prepare_links_for_render(
        link_items, max_main=max_main, max_other=max_other
    )
    lines = [
        f"### {p.subject}",
        "",
        f"- **From:** {p.sender} · **Date:** {_format_date(p.date_parsed)} · "
        f"**Read:** {_format_read_time(p.read_time_minutes)} · **Type:** {ntype}",
        f"- **Folder:** {_folder_display_name(p.folder_name)}",
        "",
    ]
    if entry.summary:
        lines.append(entry.summary)
        lines.append("")
    hidden_count = len(bundle.hidden_links)
    if bundle.main_links:
        lines.append("**Key links:**")
        for link in bundle.main_links:
            lines.append(render_link_markdown(link))
        lines.append("")
    if bundle.other_links:
        lines.append("**Other links:**")
        for link in bundle.other_links:
            lines.append(render_link_markdown(link))
        lines.append("")
    if hidden_count > 0:
        lines.append(f"**{_hidden_link_cue_text(hidden_count)}:**")
        for link in bundle.hidden_links:
            lines.append(render_link_markdown(link))
        lines.append("")
    return "\n".join(lines)


def render_markdown(report: DigestReport, max_display_links: int) -> str:
    gen_date = report.generated_at.strftime("%Y-%m-%d")
    ws = report.window_start.strftime("%Y-%m-%d")
    we = report.window_end.strftime("%Y-%m-%d")
    total = report.stats.dated_included + report.stats.undated_needing_review
    lines = [
        f"![{ROLLUP_TITLE}]({LOGO_FILENAME})",
        "",
        f"# {ROLLUP_TITLE} — {gen_date}",
        "",
        f"_Week of {ws} to {we} · {total} newsletters_",
        "",
    ]
    lines.extend(_render_toc_md(report))
    for folder, entries in sorted(report.dated_by_folder.items()):
        lines.append(f"## {_folder_display_name(folder)}")
        lines.append("")
        for entry in entries:
            lines.append(_render_entry_md(entry, max_display_links))
    if report.undated:
        lines.append("## Undated / needs review")
        lines.append("")
        for entry in report.undated:
            lines.append(_render_entry_md(entry, max_display_links))
    lines.extend(_render_run_details_md(report))
    return "\n".join(lines).rstrip() + "\n"


def _render_entry_html(
    entry: DigestEntry,
    max_display_links: int,
) -> str:
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type
    link_items = (
        list(p.link_items)
        if getattr(p, "link_items", ())
        else [
            LinkItem(href=href, text=None, context=None, source_index=index)
            for index, href in enumerate(p.links)
        ]
    )
    max_main = min(5, max_display_links)
    max_other = max(0, max_display_links - max_main)
    bundle = prepare_links_for_render(
        link_items, max_main=max_main, max_other=max_other
    )
    summary_line = (
        f"<strong>{html_module.escape(p.subject)}</strong> — "
        f"{html_module.escape(_display_sender(p.sender))} — "
        f"{_format_read_time(p.read_time_minutes)}"
    )
    parts = [
        "<details class='newsletter-card' data-newsletter-card>"
        f"<summary>{summary_line}</summary><div class='card-body'>"
    ]
    parts.append(
        f"<p class='item-type'>{html_module.escape(_format_newsletter_type(ntype))}</p>"
    )
    if entry.summary:
        parts.append(
            f"<div class='summary'>{_render_summary_html(entry.summary)}</div>"
        )
    hidden_count = len(bundle.hidden_links)
    if bundle.main_links:
        parts.append("<p><strong>Key links:</strong></p>")
        parts.append("<ul>")
        for link in bundle.main_links:
            parts.append(render_link_html(link))
        parts.append("</ul>")
    if bundle.other_links:
        parts.append(
            "<details class='other-links'><summary>Other links</summary><ul>"
        )
        for link in bundle.other_links:
            parts.append(render_link_html(link))
        parts.append("</ul></details>")
    if hidden_count > 0:
        parts.append(
            f"<details class='hidden-links'><summary>"
            f"{html_module.escape(_hidden_link_cue_text(hidden_count))}"
            f"</summary><ul>"
        )
        for link in bundle.hidden_links:
            parts.append(render_link_html(link))
        parts.append("</ul></details>")
    parts.append("</div></details>")
    return "\n".join(parts)


_EXPAND_COLLAPSE_SCRIPT = """<script>
document.getElementById('expand-all-cards')?.addEventListener('click', function () {
  document.querySelectorAll('details.newsletter-card').forEach(function (el) { el.open = true; });
});
document.getElementById('collapse-all-cards')?.addEventListener('click', function () {
  document.querySelectorAll('details.newsletter-card').forEach(function (el) { el.open = false; });
});
</script>"""


def render_html(report: DigestReport, max_display_links: int) -> str:
    window_label = _format_window_range(report.window_start, report.window_end)
    total = report.stats.dated_included + report.stats.undated_needing_review
    anchor_map = _folder_anchor_map(report)
    body_parts = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        f"<title>{ROLLUP_TITLE} — {window_label}</title>",
        f"<link rel='icon' href='{FAVICON_FILENAME}' type='image/x-icon'>",
        "<style>",
        "body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;line-height:1.5;}",
        ".rollup-header{margin-bottom:0.5rem;}",
        ".rollup-logo{height:120px;width:auto;border-radius:6px;}",
        ".rollup-subhead{margin:0.25rem 0 1rem;font-size:1.05rem;color:#444;}",
        ".rollup-toc{margin:1rem 0;}",
        ".rollup-toc ul{margin:0.35rem 0;padding-left:1.25rem;}",
        ".rollup-controls{margin:0 0 0.5rem;font-size:0.9rem;}",
        ".rollup-controls button{font:inherit;cursor:pointer;background:none;border:none;padding:0;color:#06c;text-decoration:underline;}",
        "details.newsletter-card{margin:1rem 0;border:1px solid #ddd;border-radius:6px;padding:0.5rem 1rem;}",
        "details.newsletter-card>summary{cursor:pointer;font-weight:400;}",
        "details.newsletter-card>summary strong{font-weight:600;}",
        "details.run-details{margin:1rem 0;border:none;padding:0;border-top:1px solid #eee;}",
        "details.run-details>summary{cursor:pointer;font-weight:600;font-size:0.9rem;color:#666;}",
        "details.other-links{border:none;padding:0;margin-top:0.5rem;}",
        "details.other-links>summary{cursor:pointer;font-weight:600;font-size:0.95rem;}",
        "details.hidden-links{border:none;padding:0;margin-top:0.5rem;}",
        "details.hidden-links>summary{cursor:pointer;font-weight:600;font-size:0.95rem;color:#666;}",
        "#undated{border:1px solid #c90;border-radius:6px;padding:0.5rem 1rem;background:#fffbe6;}",
        ".folder-byline{margin:-0.25rem 0 0.75rem;font-size:0.95rem;color:#555;}",
        ".run-details-section{margin-top:1rem;}",
        ".run-details-heading{font-size:1rem;margin:0 0 0.5rem;font-weight:600;color:#444;}",
        ".stats{background:#f5f5f5;padding:1rem;border-radius:6px;white-space:pre-wrap;font-size:0.9rem;}",
        ".summary h3{font-size:1rem;margin:1rem 0 0.35rem;font-weight:600;}",
        ".summary ul{margin:0.35rem 0 0.75rem;padding-left:1.25rem;}",
        ".summary li{margin:0.2rem 0;}",
        ".summary p{margin:0.5rem 0;}",
        ".link-domain{color:#666;font-size:0.9em;}",
        ".item-type{margin:0 0 0.75rem;font-size:0.9rem;color:#666;}",
        _folder_accent_css(),
        ".summary-metadata table{border-collapse:collapse;margin:1rem 0;}",
        ".summary-metadata th,.summary-metadata td{border:1px solid #ddd;padding:0.25rem 0.5rem;text-align:left;}",
        "</style></head><body>",
        "<header class='rollup-header'>",
        f"<img class='rollup-logo' src='{LOGO_FILENAME}' alt='{ROLLUP_TITLE} logo'>",
        "</header>",
        f"<p class='rollup-subhead'><em>{html_module.escape(window_label)} · {total} newsletters</em></p>",
        _render_toc_html(anchor_map, include_undated=bool(report.undated)),
    ]
    section_ids = {folder: section_id for folder, section_id, _ in anchor_map}
    for folder, entries in sorted(report.dated_by_folder.items()):
        section_id = section_ids[folder]
        accent_class = _folder_accent_class(folder)
        sorted_entries = _sort_entries_by_read_time(entries)
        body_parts.append(
            f"<section id='{html_module.escape(section_id)}' "
            f"class='folder-section {accent_class}'>"
            f"<h2>{html_module.escape(_folder_display_name(folder))}</h2>"
            f"{_render_section_byline_html(entries)}"
        )
        for entry in sorted_entries:
            body_parts.append(_render_entry_html(entry, max_display_links))
        body_parts.append("</section>")
    if report.undated:
        body_parts.append(
            "<section id='undated'><h2>Undated / needs review</h2>"
            f"{_render_section_byline_html(report.undated)}"
        )
        for entry in _sort_entries_by_read_time(report.undated):
            body_parts.append(_render_entry_html(entry, max_display_links))
        body_parts.append("</section>")
    body_parts.append(_render_run_details_html(report))
    body_parts.append(_EXPAND_COLLAPSE_SCRIPT)
    body_parts.append("</body></html>")
    return "\n".join(body_parts)


def write_branding_assets(output_dir: Path) -> None:
    """Copy logo and favicon beside rollup output files."""
    from rollup.assets import asset_bytes

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / LOGO_FILENAME).write_bytes(asset_bytes(LOGO_FILENAME))
    (output_dir / FAVICON_FILENAME).write_bytes(asset_bytes(FAVICON_FILENAME))


def digest_output_stem(
    generated_at: datetime, variant_name: str | None = None
) -> str:
    """Filename stem for digest outputs (date + time so same-day runs do not overwrite)."""
    timestamp = generated_at.strftime("%Y-%m-%d-%H%M%S")
    suffix = f".{variant_name}" if variant_name else ""
    return f"{timestamp}-newsletter-digest{suffix}"


def cleanup_stale_temps(output_dir: Path) -> None:
    for path in output_dir.glob(".tmp-*"):
        try:
            path.unlink()
            logger.debug("Removed stale temp file %s", path)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)


def atomic_write_digest(
    output_dir: Path,
    generated_at: datetime,
    markdown: str,
    html_content: str,
    variant_name: str | None = None,
) -> tuple[Path, Path]:
    """Write MD and HTML atomically via temp files + rename."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_stale_temps(output_dir)
    write_branding_assets(output_dir)
    stem = digest_output_stem(generated_at, variant_name)
    final_md = output_dir / f"{stem}.md"
    final_html = output_dir / f"{stem}.html"
    tmp_md = output_dir / f".tmp-{stem}.md"
    tmp_html = output_dir / f".tmp-{stem}.html"

    paths_to_clean = (tmp_md, tmp_html, final_md, final_html)

    def _cleanup() -> None:
        for path in paths_to_clean:
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                logger.warning("Could not remove %s: %s", path, exc)

    try:
        tmp_md.write_text(markdown, encoding="utf-8")
        tmp_html.write_text(html_content, encoding="utf-8")
        tmp_md.rename(final_md)
        tmp_html.rename(final_html)
    except Exception:
        _cleanup()
        raise
    return final_md, final_html
