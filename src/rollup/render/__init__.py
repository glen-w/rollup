"""Markdown and HTML digest rendering with atomic writes.

Package layout:

* :mod:`rollup.render.model` — format-neutral decisions (ordering, anchors,
  bylines, coverage notes, attention labels, link bundling, truncation).
* :mod:`rollup.render.markdown` / :mod:`rollup.render.html` — pure emitters.
* :mod:`rollup.render.files` — output stems, branding assets, staged writes.

``from rollup.render import …`` keeps working for every name the addons, web
UI, pipeline, and tests imported from the former single module, including the
underscore-prefixed helpers.
"""

from __future__ import annotations

from rollup.render.files import (
    atomic_write_digest,
    cleanup_stale_temps,
    digest_output_stem,
    write_branding_assets,
)
from rollup.render.html import (
    inline_summary_markdown,
    render_entry_html,
    render_group_html,
    render_html,
    render_summary_html,
)
from rollup.render.markdown import (
    render_entry_markdown,
    render_group_markdown,
    render_markdown,
)
from rollup.render.model import (
    ROLLUP_TITLE,
    FolderThemeMap,
    display_sender,
    folder_accent_class,
    folder_accent_color,
    folder_display_name,
    folder_section_id,
    format_date,
    format_newsletter_type,
    format_read_time,
    format_section_byline,
    format_window_range,
    render_stats_block,
    sort_digest_items,
    sort_entries_by_read_time,
)

# Historical underscore aliases, kept for importers that predate the package.
_format_date = format_date
_display_sender = display_sender
_folder_display_name = folder_display_name
_folder_accent_class = folder_accent_class
_folder_accent_color = folder_accent_color
_folder_section_id = folder_section_id
_format_newsletter_type = format_newsletter_type
_format_read_time = format_read_time
_format_section_byline = format_section_byline
_format_window_range = format_window_range
_sort_entries_by_read_time = sort_entries_by_read_time
_sort_digest_items = sort_digest_items
_render_summary_html = render_summary_html
_inline_summary_markdown = inline_summary_markdown
_render_entry_md = render_entry_markdown
_render_entry_html = render_entry_html
_render_group_md = render_group_markdown
_render_group_html = render_group_html

__all__ = [
    "ROLLUP_TITLE",
    "FolderThemeMap",
    "atomic_write_digest",
    "cleanup_stale_temps",
    "digest_output_stem",
    "display_sender",
    "folder_accent_class",
    "folder_accent_color",
    "folder_display_name",
    "folder_section_id",
    "format_date",
    "format_newsletter_type",
    "format_read_time",
    "format_section_byline",
    "format_window_range",
    "inline_summary_markdown",
    "render_entry_html",
    "render_entry_markdown",
    "render_group_html",
    "render_group_markdown",
    "render_html",
    "render_markdown",
    "render_stats_block",
    "render_summary_html",
    "sort_digest_items",
    "sort_entries_by_read_time",
    "write_branding_assets",
]
