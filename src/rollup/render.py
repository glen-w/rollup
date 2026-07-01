"""Markdown and HTML digest rendering with atomic writes."""

from __future__ import annotations

import html as html_module
import logging
from datetime import datetime
from pathlib import Path

from rollup.models import DigestEntry, DigestReport, DigestStats

logger = logging.getLogger(__name__)


def _format_date(dt: datetime | None) -> str:
    if dt is None:
        return "undated"
    return dt.strftime("%Y-%m-%d %H:%M")


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
        f"cache {stats.summaries_cache} · fallback {stats.summaries_fallback}"
    )


def _render_entry_md(entry: DigestEntry, max_display_links: int) -> str:
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type
    lines = [
        f"### {p.subject}",
        "",
        f"- **From:** {p.sender} · **Date:** {_format_date(p.date_parsed)} · "
        f"**Read:** {p.read_time_minutes} min · **Type:** {ntype}",
        f"- **Folder:** {p.folder_name}",
        "",
    ]
    if entry.summary:
        lines.append(entry.summary)
        lines.append("")
    if p.links:
        lines.append("**Top links:**")
        for link in p.links[:max_display_links]:
            lines.append(f"- <{link}>")
        lines.append("")
    return "\n".join(lines)


def render_markdown(report: DigestReport, max_display_links: int) -> str:
    gen_date = report.generated_at.strftime("%Y-%m-%d")
    ws = report.window_start.strftime("%Y-%m-%d")
    we = report.window_end.strftime("%Y-%m-%d")
    total = report.stats.dated_included + report.stats.undated_needing_review
    lines = [
        f"# Newsletter Digest — {gen_date}",
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
    for folder, entries in sorted(report.dated_by_folder.items()):
        lines.append(f"## {folder}")
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
    summary_line = (
        f"{html_module.escape(p.subject)} — {html_module.escape(p.sender)} — "
        f"{html_module.escape(_format_date(p.date_parsed))} — "
        f"{p.read_time_minutes} min — {ntype}"
    )
    parts = [f"<details><summary>{summary_line}</summary><div class='card-body'>"]
    parts.append(f"<p><strong>Folder:</strong> {html_module.escape(p.folder_name)}</p>")
    if entry.summary:
        esc = html_module.escape(entry.summary)
        parts.append(f"<p class='summary'>{esc}</p>")
    if p.links:
        parts.append("<ul>")
        for link in p.links[:max_display_links]:
            esc = html_module.escape(link)
            parts.append(f'<li><a href="{esc}" rel="noopener">{esc}</a></li>')
        parts.append("</ul>")
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
        f"<title>Newsletter Digest — {gen_date}</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;line-height:1.5;}",
        "details{margin:1rem 0;border:1px solid #ddd;border-radius:6px;padding:0.5rem 1rem;}",
        "summary{cursor:pointer;font-weight:600;}",
        "#undated{border-color:#c90;background:#fffbe6;}",
        ".stats{background:#f5f5f5;padding:1rem;border-radius:6px;white-space:pre-wrap;font-size:0.9rem;}",
        ".summary{white-space:pre-wrap;}",
        "</style></head><body>",
        f"<h1>Newsletter Digest — {gen_date}</h1>",
        f"<p><em>Week of {ws} to {we} · {total} newsletters</em></p>",
        f"<div class='stats'>{html_module.escape(render_stats_block(report.stats))}</div>",
    ]
    for folder, entries in sorted(report.dated_by_folder.items()):
        body_parts.append(f"<h2>{html_module.escape(folder)}</h2>")
        for entry in entries:
            body_parts.append(_render_entry_html(entry, max_display_links))
    if report.undated:
        body_parts.append("<section id='undated'><h2>Undated / needs review</h2>")
        for entry in report.undated:
            body_parts.append(_render_entry_html(entry, max_display_links))
        body_parts.append("</section>")
    body_parts.append("</body></html>")
    return "\n".join(body_parts)


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
) -> tuple[Path, Path]:
    """Write MD and HTML atomically via temp files + rename."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_stale_temps(output_dir)
    date_str = generated_at.strftime("%Y-%m-%d")
    final_md = output_dir / f"{date_str}-newsletter-digest.md"
    final_html = output_dir / f"{date_str}-newsletter-digest.html"
    tmp_md = output_dir / f".tmp-{date_str}-newsletter-digest.md"
    tmp_html = output_dir / f".tmp-{date_str}-newsletter-digest.html"

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
