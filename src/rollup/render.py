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

logger = logging.getLogger(__name__)

ROLLUP_TITLE = "Rollup"

FOLDER_EMOJI: dict[str, str] = {
    "brainfood": "🧠",
    "enviro": "🌲",
    "hoops": "🏀",
    "tech": "💻",
    "misc": "📬",
    "trackerwall": "📰",
}


def _format_date(dt: datetime | None) -> str:
    if dt is None:
        return "undated"
    return dt.strftime("%Y-%m-%d %H:%M")


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


def _format_read_time(minutes: int) -> str:
    return f"🕐 {minutes} min"


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
            "## Summary Variant",
            "",
            f"- **Profile:** {metadata.variant_name}",
            f"- **Models used:** {', '.join(metadata.models_used) or 'none'}",
            f"- **Summaries:** Ollama {metadata.summaries_ollama} · cache {metadata.summaries_cache} · fallback {metadata.summaries_fallback} · errors {metadata.summaries_errors}",
            "",
        ]
    else:
        lines = [
            "## Summary Routing",
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
    title = "Summary Variant" if metadata.variant_name else "Summary Routing"
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
    return f"<section class='summary-metadata'><h2>{title}</h2><ul>{''.join(items)}</ul>{table}</section>"


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
        "## Stats",
        "",
        "```",
        render_stats_block(report.stats),
        "```",
        "",
    ]
    lines.extend(_render_summary_metadata_md(report))
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
    return "\n".join(lines).rstrip() + "\n"


def _render_entry_html(entry: DigestEntry, max_display_links: int) -> str:
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
        f"{html_module.escape(p.subject)} — {html_module.escape(_display_sender(p.sender))} — "
        f"{html_module.escape(_format_date(p.date_parsed))} — "
        f"{_format_read_time(p.read_time_minutes)} — {ntype}"
    )
    parts = [f"<details><summary>{summary_line}</summary><div class='card-body'>"]
    parts.append(
        f"<p><strong>Folder:</strong> {html_module.escape(_folder_display_name(p.folder_name))}</p>"
    )
    if entry.summary:
        parts.append(
            f"<div class='summary'>{_render_summary_html(entry.summary)}</div>"
        )
    if bundle.main_links:
        parts.append("<p><strong>Key links:</strong></p>")
        parts.append("<ul>")
        for link in bundle.main_links:
            parts.append(render_link_html(link))
        parts.append("</ul>")
    if bundle.other_links:
        parts.append("<details class='other-links'><summary>Other links</summary><ul>")
        for link in bundle.other_links:
            parts.append(render_link_html(link))
        parts.append("</ul></details>")
    parts.append("</div></details>")
    return "\n".join(parts)


def render_html(report: DigestReport, max_display_links: int) -> str:
    gen_date = report.generated_at.strftime("%Y-%m-%d")
    ws = report.window_start.strftime("%Y-%m-%d")
    we = report.window_end.strftime("%Y-%m-%d")
    total = report.stats.dated_included + report.stats.undated_needing_review
    body_parts = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        f"<title>{ROLLUP_TITLE} — {gen_date}</title>",
        f"<link rel='icon' href='{FAVICON_FILENAME}' type='image/x-icon'>",
        "<style>",
        "body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;line-height:1.5;}",
        ".rollup-header{display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;}",
        ".rollup-logo{height:48px;width:auto;border-radius:6px;}",
        "details{margin:1rem 0;border:1px solid #ddd;border-radius:6px;padding:0.5rem 1rem;}",
        "summary{cursor:pointer;font-weight:600;}",
        "#undated{border-color:#c90;background:#fffbe6;}",
        ".stats{background:#f5f5f5;padding:1rem;border-radius:6px;white-space:pre-wrap;font-size:0.9rem;}",
        ".summary h3{font-size:1rem;margin:1rem 0 0.35rem;font-weight:600;}",
        ".summary ul{margin:0.35rem 0 0.75rem;padding-left:1.25rem;}",
        ".summary li{margin:0.2rem 0;}",
        ".summary p{margin:0.5rem 0;}",
        ".link-domain{color:#666;font-size:0.9em;}",
        ".other-links{border:none;padding:0;margin-top:0.5rem;}",
        ".summary-metadata table{border-collapse:collapse;margin:1rem 0;}",
        ".summary-metadata th,.summary-metadata td{border:1px solid #ddd;padding:0.25rem 0.5rem;text-align:left;}",
        "</style></head><body>",
        "<header class='rollup-header'>",
        f"<img class='rollup-logo' src='{LOGO_FILENAME}' alt='{ROLLUP_TITLE} logo'>",
        f"<h1>{ROLLUP_TITLE} — {gen_date}</h1>",
        "</header>",
        f"<p><em>Week of {ws} to {we} · {total} newsletters</em></p>",
        f"<div class='stats'>{html_module.escape(render_stats_block(report.stats))}</div>",
        _render_summary_metadata_html(report),
    ]
    for folder, entries in sorted(report.dated_by_folder.items()):
        body_parts.append(
            f"<h2>{html_module.escape(_folder_display_name(folder))}</h2>"
        )
        for entry in entries:
            body_parts.append(_render_entry_html(entry, max_display_links))
    if report.undated:
        body_parts.append("<section id='undated'><h2>Undated / needs review</h2>")
        for entry in report.undated:
            body_parts.append(_render_entry_html(entry, max_display_links))
        body_parts.append("</section>")
    body_parts.append("</body></html>")
    return "\n".join(body_parts)


def write_branding_assets(output_dir: Path) -> None:
    """Copy logo and favicon beside rollup output files."""
    from rollup.assets import asset_bytes

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / LOGO_FILENAME).write_bytes(asset_bytes(LOGO_FILENAME))
    (output_dir / FAVICON_FILENAME).write_bytes(asset_bytes(FAVICON_FILENAME))


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
    date_str = generated_at.strftime("%Y-%m-%d")
    suffix = f".{variant_name}" if variant_name else ""
    final_md = output_dir / f"{date_str}-newsletter-digest{suffix}.md"
    final_html = output_dir / f"{date_str}-newsletter-digest{suffix}.html"
    tmp_md = output_dir / f".tmp-{date_str}-newsletter-digest{suffix}.md"
    tmp_html = output_dir / f".tmp-{date_str}-newsletter-digest{suffix}.html"

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
