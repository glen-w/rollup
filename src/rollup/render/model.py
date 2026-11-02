"""Format-neutral render model.

Everything that *decides* what a digest looks like lives here: ordering,
anchors, bylines, coverage notes, attention labels, link bundling, truncation,
and the labels shared by Markdown and HTML. The emitters in
:mod:`rollup.render.markdown` and :mod:`rollup.render.html` only format the
views built here. They must not sort, filter, or branch on report content.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr

from rollup.addons.offline_text import (
    COMPACT_HTML_STREAM_PREVIEW_MAX,
    COMPACT_PREVIEW_MAX,
    COMPACT_SUBJECT_MAX,
    truncate_with_ellipsis,
)
from rollup.final_review import format_final_review_digest_summary
from rollup.folder_theme import (
    FolderThemeOverride,
    folder_display_name,
    folder_slug,
    sort_folder_names,
    theme_for,
)
from rollup.links import LinkRenderBundle, prepare_links_for_render
from rollup.models import (
    CoverageCluster,
    DigestEntry,
    DigestGroup,
    DigestItem,
    DigestReport,
    DigestStats,
    LinkItem,
)
from rollup.reddit.summary import reddit_entry_display
from rollup.summarize import clean_summary_output

ROLLUP_TITLE = "Rollup"

FolderThemeMap = dict[str, FolderThemeOverride]
CoverageIndex = tuple[dict[str, CoverageCluster], dict[str, str]]

UNDATED_SECTION_ID = "undated"
UNDATED_SECTION_TITLE = "Undated / needs review"
GLANCE_DEFAULT_TITLE = "This window at a glance"
START_HERE_TITLE = "Start here"
CONTENTS_TITLE = "Contents"
RUN_DETAILS_TITLE = "Digest generation details"


# --- scalar labels ---------------------------------------------------------------


def format_date(dt: datetime | None) -> str:
    """Public date label for digest / addon renderers."""
    if dt is None:
        return "undated"
    return dt.strftime("%Y-%m-%d %H:%M")


def format_day(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "undated"


def format_window_range(start: datetime, end: datetime) -> str:
    """Human-readable digest window, e.g. 23-30 July 2026 or 30 July - 6 August 2026."""
    start_day = start.day
    end_day = end.day
    if start.year == end.year and start.month == end.month:
        return f"{start_day}-{end_day} {end.strftime('%B %Y')}"
    if start.year == end.year:
        return f"{start_day} {start.strftime('%B')} - {end_day} {end.strftime('%B %Y')}"
    return f"{start_day} {start.strftime('%B %Y')} - {end_day} {end.strftime('%B %Y')}"


def display_sender(sender: str) -> str:
    """Return a human-readable sender name without the email address."""
    name, addr = parseaddr(sender)
    display = name.strip()
    if display:
        return display
    if addr:
        return addr.split("@", 1)[0]
    return sender.strip() or "(unknown)"


def format_read_time(minutes: int) -> str:
    """Public read-time label for digest / addon renderers."""
    return f"🕐 {minutes} min"


def format_newsletter_type(ntype: str) -> str:
    parts = ntype.split("_")
    if len(parts) == 1:
        return parts[0].capitalize()
    return f"{parts[0].capitalize()} {' '.join(parts[1:])}"


def folder_accent_class(folder: str) -> str:
    slug = folder_slug(folder)
    if slug == "folder" and not folder.strip():
        return "folder-accent-default"
    return f"folder-accent-{slug}"


def folder_accent_color(folder: str, overrides: FolderThemeMap | None = None) -> str:
    return theme_for(folder, overrides).accent


def folder_section_id(folder: str) -> str:
    return f"folder-{folder_slug(folder)}"


def truncate(text: str, max_chars: int) -> str:
    return truncate_with_ellipsis(text, max_chars)


def hidden_link_cue_text(hidden_count: int) -> str:
    return f"+{hidden_count} more links in original"


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
        f"LiteLLM {stats.summaries_litellm} · "
        f"cache {stats.summaries_cache} · fallback {stats.summaries_fallback} · "
        f"errors {stats.summaries_errors}"
    )


def summaries_line(meta) -> str:
    """Shared 'Ollama N · LiteLLM N · …' text for summary metadata blocks."""
    return (
        f"Ollama {meta.summaries_ollama} · LiteLLM {meta.summaries_litellm} · "
        f"cache {meta.summaries_cache} · fallback {meta.summaries_fallback} · "
        f"errors {meta.summaries_errors}"
    )


# --- ordering -------------------------------------------------------------------


def iter_item_entries(items: tuple[DigestItem, ...]) -> Iterator[DigestEntry]:
    for item in items:
        if isinstance(item, DigestGroup):
            yield from item.entries
        else:
            yield item


def sort_entries_by_read_time(
    entries: tuple[DigestItem, ...],
    *,
    priority_for=None,
) -> tuple[DigestItem, ...]:
    from rollup.source_policy import priority_sort_prefix

    def key(item: DigestItem) -> tuple:
        if isinstance(item, DigestGroup):
            pri = 0
            if priority_for is not None:
                pols = [priority_for(e) for e in item.entries]
                pri = max((int(p or 0) for p in pols), default=0)
            minutes = sum(e.classified.parsed.read_time_minutes for e in item.entries)
            return (
                *priority_sort_prefix(pri),
                0,  # groups before standalone entries
                minutes,
                item.display_name.lower(),
            )
        pri = 0
        if priority_for is not None:
            pri = int(priority_for(item) or 0)
        return (
            *priority_sort_prefix(pri),
            1,
            item.classified.parsed.read_time_minutes,
            item.classified.parsed.subject.lower(),
        )

    return tuple(sorted(entries, key=key))


def sort_digest_items(items: tuple[DigestItem, ...]) -> tuple[DigestItem, ...]:
    if not any(
        getattr(entry, "attention_band", None) is not None for entry in iter_item_entries(items)
    ):
        return sort_entries_by_read_time(items)
    from rollup.attention.annotate import sort_items_by_attention

    return sort_items_by_attention(items)


# --- sections and anchors -------------------------------------------------------


def format_section_byline(entries: tuple[DigestItem, ...]) -> str:
    flat_count = 0
    total_minutes = 0
    for item in entries:
        if isinstance(item, DigestGroup):
            flat_count += len(item.entries)
            total_minutes += sum(e.classified.parsed.read_time_minutes for e in item.entries)
        else:
            flat_count += 1
            total_minutes += item.classified.parsed.read_time_minutes
    newsletter_word = "newsletter" if flat_count == 1 else "newsletters"
    minute_word = "minute" if total_minutes == 1 else "minutes"
    return f"{flat_count} {newsletter_word}, {total_minutes} {minute_word} reading time"


def folder_anchor_map(
    report: DigestReport,
    folder_themes: FolderThemeMap | None = None,
) -> list[tuple[str, str, int]]:
    slug_counts: dict[str, int] = {}
    anchors: list[tuple[str, str, int]] = []
    for folder in sort_folder_names(report.dated_by_folder.keys(), folder_themes):
        entries = report.dated_by_folder[folder]
        base = folder_slug(folder)
        slug_counts[base] = slug_counts.get(base, 0) + 1
        count = slug_counts[base]
        section_id = folder_section_id(folder) if count == 1 else f"folder-{base}-{count}"
        anchors.append((folder, section_id, len(entries)))
    return anchors


# --- attention and coverage -----------------------------------------------------


def continuity_chrome(entry: DigestEntry) -> str | None:
    label = getattr(entry, "continuity_label", None)
    if label == "continuing":
        return "Still developing"
    if label == "repeat":
        return "Seen again"
    return None


def attention_label(entry: DigestEntry) -> str | None:
    band = entry.attention_band
    if not band:
        return None
    from rollup.attention.bands import BAND_LABELS

    return BAND_LABELS.get(band, band)


def coverage_index(report: DigestReport) -> CoverageIndex:
    from rollup.briefing import iter_digest_entries

    clusters = {c.cluster_id: c for c in report.coverage_clusters}
    subjects: dict[str, str] = {}
    for items in report.dated_by_folder.values():
        for entry in iter_digest_entries(items):
            subjects[entry.classified.parsed.message_key] = entry.classified.parsed.subject
    for entry in iter_digest_entries(report.undated):
        subjects[entry.classified.parsed.message_key] = entry.classified.parsed.subject
    return clusters, subjects


@dataclass(frozen=True)
class CoverageNote:
    """'Covered N ways: theme' on the representative, 'Also in this window: rep' elsewhere."""

    label: str
    text: str


def coverage_note(entry: DigestEntry, coverage: CoverageIndex | None) -> CoverageNote | None:
    if not coverage or not entry.coverage_cluster_id:
        return None
    clusters, subjects = coverage
    cluster = clusters.get(entry.coverage_cluster_id)
    if cluster is None:
        return None
    n = len(cluster.member_keys)
    if entry.classified.parsed.message_key == cluster.representative_key:
        return CoverageNote(label=f"Covered {n} ways", text=cluster.theme_label)
    rep = subjects.get(cluster.representative_key, cluster.theme_label)
    return CoverageNote(label="Also in this window", text=rep)


def start_here_entries(report: DigestReport) -> tuple[DigestEntry, ...]:
    from rollup.attention.annotate import high_relevance_entries

    return tuple(high_relevance_entries(report))


# --- entry and group views -------------------------------------------------------


def link_bundle(entry: DigestEntry, max_display_links: int) -> LinkRenderBundle:
    p = entry.classified.parsed
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
    return prepare_links_for_render(link_items, max_main=max_main, max_other=max_other)


def cleaned_summary(entry: DigestEntry) -> str:
    if not entry.summary:
        return ""
    return clean_summary_output(entry.summary) or ""


@dataclass(frozen=True)
class GroupMemberView:
    """One member row inside a compact group (stream / editions / subreddit)."""

    entry: DigestEntry
    day: str
    subject: str
    preview: str
    summary: str  # subreddit digests carry a per-post summary instead of a preview


def group_member_views(group: DigestGroup, *, html: bool = False) -> tuple[GroupMemberView, ...]:
    rows: list[GroupMemberView] = []
    for entry in group.entries:
        p = entry.classified.parsed
        if group.group_type == "subreddit_digest":
            subject, summary = reddit_entry_display(entry)
            rows.append(GroupMemberView(entry, format_day(p.date_parsed), subject, "", summary or ""))
            continue
        preview_cap = (
            COMPACT_HTML_STREAM_PREVIEW_MAX
            if html and group.group_type == "notification_stream"
            else COMPACT_PREVIEW_MAX
        )
        rows.append(
            GroupMemberView(
                entry,
                format_day(p.date_parsed),
                truncate(p.subject, COMPACT_SUBJECT_MAX),
                truncate(entry.summary or p.preview or "", preview_cap),
                "",
            )
        )
    return tuple(rows)


def group_title(group: DigestGroup) -> str:
    n = len(group.entries)
    noun = {
        "subreddit_digest": "posts",
        "notification_stream": "updates",
    }.get(group.group_type, "editions")
    return f"{group.display_name} — {n} {noun} this week"


def group_summary_label(group: DigestGroup) -> str:
    if group.group_type == "subreddit_digest":
        return "Roundup"
    if group.group_type == "notification_stream":
        return "This week"
    return "Edition roundup"


def group_fallback_intro(group: DigestGroup) -> str:
    n = len(group.entries)
    if group.group_type == "notification_stream":
        return f"Grouped notification stream ({n} messages). Newest first."
    return f"Grouped daily editions ({n} messages)."


# --- run details ----------------------------------------------------------------


def final_review_text(report: DigestReport) -> str | None:
    if report.final_review is None:
        return None
    return format_final_review_digest_summary(report.final_review)


__all__ = [
    "CONTENTS_TITLE",
    "GLANCE_DEFAULT_TITLE",
    "ROLLUP_TITLE",
    "RUN_DETAILS_TITLE",
    "START_HERE_TITLE",
    "UNDATED_SECTION_ID",
    "UNDATED_SECTION_TITLE",
    "CoverageIndex",
    "CoverageNote",
    "FolderThemeMap",
    "GroupMemberView",
    "attention_label",
    "cleaned_summary",
    "coverage_index",
    "coverage_note",
    "display_sender",
    "final_review_text",
    "folder_accent_class",
    "folder_accent_color",
    "folder_anchor_map",
    "folder_display_name",
    "folder_section_id",
    "format_date",
    "format_day",
    "format_newsletter_type",
    "format_read_time",
    "format_section_byline",
    "format_window_range",
    "group_fallback_intro",
    "group_member_views",
    "group_summary_label",
    "group_title",
    "hidden_link_cue_text",
    "iter_item_entries",
    "link_bundle",
    "render_stats_block",
    "sort_digest_items",
    "sort_entries_by_read_time",
    "sort_folder_names",
    "start_here_entries",
    "summaries_line",
    "truncate",
]
