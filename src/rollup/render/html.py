"""HTML emitter over the render model. Formats only; decides nothing."""

from __future__ import annotations

import html as html_module
import re

from rollup.assets import FAVICON_FILENAME, LOGO_FILENAME
from rollup.folder_theme import folder_accent_css
from rollup.links import render_link_html
from rollup.links_sanitize import sanitize_http_url
from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport
from rollup.render import model as m
from rollup.render.model import CoverageIndex, FolderThemeMap

esc = html_module.escape

# --- inline summary markdown -> HTML ---------------------------------------------

_INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_INLINE_ITALIC_RE = re.compile(r"\*(?!\*)(.+?)(?<!\*)\*")
_INLINE_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!\w)_(.+?)_(?!\w)")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_ANCHOR_ATTRS = 'target="_blank" rel="noopener noreferrer"'


def _replace_inline_link(match: re.Match[str]) -> str:
    label = match.group(1)
    # Summary text is html-escaped before markdown rewrite; unescape for checks.
    href_raw = html_module.unescape(match.group(2)).strip()
    safe = sanitize_http_url(href_raw)
    if safe is None:
        return label
    return f'<a href="{esc(safe, quote=True)}" {_ANCHOR_ATTRS}>{label}</a>'


def inline_summary_markdown(text: str) -> str:
    text = _INLINE_BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _INLINE_ITALIC_RE.sub(r"<em>\1</em>", text)
    text = _INLINE_ITALIC_UNDERSCORE_RE.sub(r"<em>\1</em>", text)
    return _INLINE_LINK_RE.sub(_replace_inline_link, text)


def render_summary_html(text: str) -> str:
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
            parts.append(f"<h3>{inline_summary_markdown(esc(stripped[4:]))}</h3>")
        elif stripped.startswith("## "):
            parts.append(f"<h3>{inline_summary_markdown(esc(stripped[3:]))}</h3>")
        elif stripped.startswith("# "):
            parts.append(f"<h3>{inline_summary_markdown(esc(stripped[2:]))}</h3>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            items: list[str] = []
            while i < len(lines):
                item = lines[i].strip()
                if item.startswith("- ") or item.startswith("* "):
                    items.append(f"<li>{inline_summary_markdown(esc(item[2:]))}</li>")
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
                if not item or item.startswith("#") or item.startswith("- ") or item.startswith("* "):
                    break
                para_lines.append(item)
                i += 1
            parts.append(f"<p>{inline_summary_markdown(esc(' '.join(para_lines)))}</p>")
            continue
        i += 1
    return "".join(parts)


# --- page chrome -------------------------------------------------------------------


def _synthesis(report: DigestReport) -> str:
    synthesis = report.briefing_synthesis
    if synthesis is None or not synthesis.bullets:
        return ""
    title = esc(synthesis.headline or m.GLANCE_DEFAULT_TITLE)
    parts = [f"<section class='briefing-synthesis'><h2>{title}</h2><ul>"]
    parts.extend(f"<li>{esc(bullet)}</li>" for bullet in synthesis.bullets)
    parts.append("</ul>")
    if synthesis.what_to_skip:
        parts.append("<p><strong>Safely skip</strong></p><ul>")
        parts.extend(f"<li>{esc(bullet)}</li>" for bullet in synthesis.what_to_skip)
        parts.append("</ul>")
    parts.append("</section>")
    return "\n".join(parts)


def _start_here(report: DigestReport) -> str:
    entries = m.start_here_entries(report)
    if not entries:
        return ""
    parts = [f"<section class='start-here'><h2>{m.START_HERE_TITLE}</h2><ul>"]
    for entry in entries:
        p = entry.classified.parsed
        parts.append(
            f"<li><strong>{esc(p.subject)}</strong> — {esc(m.display_sender(p.sender))}</li>"
        )
    parts.append("</ul></section>")
    return "\n".join(parts)


def _toc(
    anchor_map: list[tuple[str, str, int]],
    *,
    include_undated: bool,
    folder_themes: FolderThemeMap | None,
) -> str:
    items = [
        f"<li><a href='#{esc(section_id)}'>"
        f"{esc(m.folder_display_name(folder, folder_themes))} ({count})</a></li>"
        for folder, section_id, count in anchor_map
    ]
    if include_undated:
        items.append(f"<li><a href='#{m.UNDATED_SECTION_ID}'>{m.UNDATED_SECTION_TITLE}</a></li>")
    controls = (
        "<p class='rollup-controls'>"
        "<button type='button' id='expand-all-cards' "
        "aria-label='Expand all newsletter cards'>Expand all</button>"
        " · "
        "<button type='button' id='collapse-all-cards' "
        "aria-label='Collapse all newsletter cards'>Collapse all</button>"
        "</p>"
    )
    return f"<nav class='rollup-toc' aria-label='{m.CONTENTS_TITLE}'>{controls}<ul>{''.join(items)}</ul></nav>"


def _section_byline(entries) -> str:
    return f"<p class='folder-byline'><em>{esc(m.format_section_byline(entries))}</em></p>"


def _summary_metadata(report: DigestReport) -> str:
    metadata = report.summary_metadata
    if metadata is None:
        return ""
    title = "Summary variant" if metadata.variant_name else "Summary routing"
    items = []
    if metadata.variant_name:
        items.append(f"<li><strong>Profile:</strong> {esc(metadata.variant_name)}</li>")
    else:
        items.append(f"<li><strong>Mode:</strong> {esc(metadata.mode)}</li>")
        items.append(
            f"<li><strong>Profiles used:</strong> {esc(', '.join(metadata.profiles_used) or 'none')}</li>"
        )
    items.append(
        f"<li><strong>Models used:</strong> {esc(', '.join(metadata.models_used) or 'none')}</li>"
    )
    items.append(f"<li><strong>Summaries:</strong> {m.summaries_line(metadata)}</li>")
    table = ""
    if metadata.routing_counts:
        rows = "".join(
            "<tr>"
            f"<td>{esc(row.newsletter_type)}</td>"
            f"<td>{esc(row.profile_name)}</td>"
            f"<td>{esc(row.model)}</td>"
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
        f"<h3 class='run-details-heading'>{esc(title)}</h3>"
        f"<ul>{''.join(items)}</ul>{table}</section>"
    )


def _final_review(report: DigestReport) -> str:
    text = m.final_review_text(report)
    if text is None:
        return ""
    return (
        "<section class='run-details-section final-review'>"
        "<h3 class='run-details-heading'>Final review</h3>"
        f"<div class='stats'>{esc(text)}</div>"
        "</section>"
    )


def _run_details(report: DigestReport) -> str:
    return (
        "<details class='run-details'>"
        f"<summary>{m.RUN_DETAILS_TITLE}</summary>"
        "<section class='run-details-section stats-section'>"
        "<h3 class='run-details-heading'>Stats</h3>"
        f"<div class='stats'>{esc(m.render_stats_block(report.stats))}</div>"
        "</section>"
        f"{_summary_metadata(report)}"
        f"{_final_review(report)}"
        "</details>"
    )


# --- entries and groups ------------------------------------------------------------


def _attention_chip(entry: DigestEntry) -> str:
    label = m.attention_label(entry)
    if label is None:
        return ""
    return f"<span class='attention-chip attention-{esc(entry.attention_band or '')}'>{esc(label)}</span>"


def _continuity_chip(entry: DigestEntry) -> str:
    label = m.continuity_chrome(entry)
    if label is None:
        return ""
    return f"<span class='attention-chip'>{esc(label)}</span>"


def _coverage_note(entry: DigestEntry, coverage: CoverageIndex | None) -> str:
    note = m.coverage_note(entry, coverage)
    if note is None:
        return ""
    return (
        f"<p class='coverage-note'><span class='attention-chip'>{esc(note.label)}</span> "
        f"{esc(note.text)}</p>"
    )


def render_group_html(group: DigestGroup, max_display_links: int) -> str:
    gid = esc(group.group_id)
    title = esc(m.group_title(group))
    members = m.group_member_views(group, html=True)
    if group.group_type == "subreddit_digest":
        parts = [
            f"<section class='digest-group' aria-labelledby='group-{gid}' "
            f"data-group-type='subreddit_digest'>",
            f"<h3 id='group-{gid}'>{title}</h3>",
            f"<p class='group-chip'>Grouped · {len(group.entries)} posts</p>",
        ]
        if group.group_summary:
            parts.append(
                f"<div class='group-summary'><p><strong>{m.group_summary_label(group)}:</strong></p>"
                f"{render_summary_html(group.group_summary.strip())}</div>"
            )
        parts.append("<ol class='group-entries'>")
        for row in members:
            parts.append(f"<li><p><strong>{esc(row.day)}</strong> — {esc(row.subject)}</p>")
            if row.summary:
                parts.append(f"<div class='summary'>{render_summary_html(row.summary)}</div>")
            parts.append("</li>")
        parts.append("</ol></section>")
        return "\n".join(parts)

    parts = [
        f"<section class='digest-group' aria-labelledby='group-{gid}' "
        f"data-group-type='{esc(group.group_type)}'>",
        f"<h3 id='group-{gid}'>{title}</h3>",
        f"<p class='group-chip'>Grouped · {len(group.entries)} messages</p>",
    ]
    if group.group_summary:
        label = m.group_summary_label(group)
        parts.append(
            f"<div class='group-summary' aria-label='{esc(label)}'>"
            f"<p><strong>{esc(label)}:</strong> {esc(group.group_summary.strip())}</p></div>"
        )
    if group.group_type == "notification_stream":
        parts.append("<ul role='list' class='group-updates'>")
        for row in members:
            p = row.entry.classified.parsed
            if p.date_parsed:
                time_html = f"<time datetime='{esc(p.date_parsed.isoformat())}'>{row.day}</time>"
            else:
                time_html = "<span>undated</span>"
            preview = esc(row.preview)
            parts.append(
                f"<li><strong>{time_html}</strong> — {esc(row.subject)}"
                + (f"<br><em>{preview}</em>" if preview else "")
                + "</li>"
            )
        parts.append("</ul>")
    else:
        summary_text = esc(f"Show {len(group.entries)} messages from {group.display_name}")
        parts.append(f"<details><summary>{summary_text}</summary>")
        for entry in group.entries:
            parts.append(render_entry_html(entry, max_display_links))
        parts.append("</details>")
    parts.append("</section>")
    return "\n".join(parts)


def render_entry_html(
    entry: DigestEntry,
    max_display_links: int,
    coverage: CoverageIndex | None = None,
) -> str:
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type
    bundle = m.link_bundle(entry, max_display_links)
    summary_line = (
        f"<strong>{esc(p.subject)}</strong> — "
        f"{esc(m.display_sender(p.sender))} — "
        f"{m.format_read_time(p.read_time_minutes)}"
    )
    skip_class = " safely-skip" if entry.attention_band == "safely_skip" else ""
    parts = [
        f"<details class='newsletter-card{skip_class}' data-newsletter-card"
        f" data-attention-band='{esc(entry.attention_band or '')}'>"
        f"<summary>{summary_line}{_attention_chip(entry)}{_continuity_chip(entry)}</summary><div class='card-body'>"
    ]
    parts.append(f"<p class='item-type'>{esc(m.format_newsletter_type(ntype))}</p>")
    cover = _coverage_note(entry, coverage)
    if cover:
        parts.append(cover)
    summary = m.cleaned_summary(entry)
    if summary:
        parts.append(f"<div class='summary'>{render_summary_html(summary)}</div>")
    if bundle.main_links:
        parts.append("<p><strong>Key links:</strong></p>")
        parts.append("<ul>")
        parts.extend(render_link_html(link) for link in bundle.main_links)
        parts.append("</ul>")
    if bundle.other_links:
        parts.append("<details class='other-links'><summary>Other links</summary><ul>")
        parts.extend(render_link_html(link) for link in bundle.other_links)
        parts.append("</ul></details>")
    if bundle.hidden_links:
        parts.append(
            f"<details class='hidden-links'><summary>"
            f"{esc(m.hidden_link_cue_text(len(bundle.hidden_links)))}"
            f"</summary><ul>"
        )
        parts.extend(render_link_html(link) for link in bundle.hidden_links)
        parts.append("</ul></details>")
    parts.append("</div></details>")
    return "\n".join(parts)


def _item(item: DigestItem, max_display_links: int, coverage: CoverageIndex | None) -> str:
    if isinstance(item, DigestGroup):
        return render_group_html(item, max_display_links)
    return render_entry_html(item, max_display_links, coverage=coverage)


_EXPAND_COLLAPSE_SCRIPT = """<script>
document.getElementById('expand-all-cards')?.addEventListener('click', function () {
  document.querySelectorAll('details.newsletter-card').forEach(function (el) { el.open = true; });
});
document.getElementById('collapse-all-cards')?.addEventListener('click', function () {
  document.querySelectorAll('details.newsletter-card').forEach(function (el) { el.open = false; });
});
</script>"""

_STYLE_RULES = (
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
    "details.other-links>summary{cursor:pointer;font-weight:600;font-size:0.9rem;color:#666;}",
    "details.hidden-links{border:none;padding:0;margin-top:0.5rem;}",
    "details.hidden-links>summary{cursor:pointer;font-weight:600;font-size:0.9rem;color:#666;}",
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
    ".briefing-synthesis{margin:1rem 0;padding:0.75rem 1rem;border:1px solid #ddd;border-radius:6px;background:#fafafa;}",
    ".start-here{margin:1rem 0;padding:0.75rem 1rem;border:1px solid #cde;border-radius:6px;background:#f7fbff;}",
    ".attention-chip{display:inline-block;margin-left:0.5rem;font-size:0.75rem;font-weight:600;padding:0.1rem 0.4rem;border-radius:999px;background:#eee;color:#444;vertical-align:middle;}",
    ".attention-high_relevance{background:#dceeff;color:#084;}",
    ".attention-worth_scanning{background:#eee;color:#444;}",
    ".attention-background{background:#f3f3f3;color:#666;}",
    ".attention-safely_skip{background:#f6f0e8;color:#864;}",
    "details.newsletter-card.safely-skip{opacity:0.85;}",
    ".digest-group{margin:1rem 0;padding:0.75rem 1rem;border:1px solid #ccc;border-radius:6px;}",
    ".group-chip{font-size:0.85rem;color:#555;margin:0.25rem 0 0.75rem;}",
    ".group-updates{margin:0.5rem 0;padding-left:1.25rem;}",
    ".group-entries{padding-left:1.25rem;}",
    ".group-entries .summary{margin:0.35em 0 0.75em;}",
    ".group-summary ul{margin:0.35rem 0 0.75rem;padding-left:1.25rem;}",
)
_STYLE_RULES_TAIL = (
    ".summary-metadata table{border-collapse:collapse;margin:1rem 0;}",
    ".summary-metadata th,.summary-metadata td{border:1px solid #ddd;padding:0.25rem 0.5rem;text-align:left;}",
)


def render_html(
    report: DigestReport,
    max_display_links: int,
    folder_themes: FolderThemeMap | None = None,
) -> str:
    window_label = m.format_window_range(report.window_start, report.window_end)
    total = report.stats.dated_included + report.stats.undated_needing_review
    anchor_map = m.folder_anchor_map(report, folder_themes)
    folders = tuple(folder for folder, _, _ in anchor_map)
    body_parts = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        f"<title>{m.ROLLUP_TITLE} — {window_label}</title>",
        f"<link rel='icon' href='{FAVICON_FILENAME}' type='image/x-icon'>",
        "<style>",
        *_STYLE_RULES,
        folder_accent_css(folders, folder_themes),
        *_STYLE_RULES_TAIL,
        "</style></head><body>",
        "<header class='rollup-header'>",
        f"<img class='rollup-logo' src='{LOGO_FILENAME}' alt='{m.ROLLUP_TITLE} logo'>",
        "</header>",
        f"<p class='rollup-subhead'><em>{esc(window_label)} · {total} newsletters</em></p>",
        _synthesis(report),
        _start_here(report),
        _toc(anchor_map, include_undated=bool(report.undated), folder_themes=folder_themes),
    ]
    section_ids = {folder: section_id for folder, section_id, _ in anchor_map}
    coverage = m.coverage_index(report)
    for folder in m.sort_folder_names(report.dated_by_folder.keys(), folder_themes):
        entries = report.dated_by_folder[folder]
        body_parts.append(
            f"<section id='{esc(section_ids[folder])}' "
            f"class='folder-section {m.folder_accent_class(folder)}'>"
            f"<h2>{esc(m.folder_display_name(folder, folder_themes))}</h2>"
            f"{_section_byline(entries)}"
        )
        for item in m.sort_digest_items(entries):
            body_parts.append(_item(item, max_display_links, coverage))
        body_parts.append("</section>")
    if report.undated:
        body_parts.append(
            f"<section id='{m.UNDATED_SECTION_ID}'><h2>{m.UNDATED_SECTION_TITLE}</h2>"
            f"{_section_byline(report.undated)}"
        )
        for item in m.sort_digest_items(report.undated):
            body_parts.append(_item(item, max_display_links, coverage))
        body_parts.append("</section>")
    body_parts.append(_run_details(report))
    body_parts.append(_EXPAND_COLLAPSE_SCRIPT)
    body_parts.append("</body></html>")
    return "\n".join(body_parts)
