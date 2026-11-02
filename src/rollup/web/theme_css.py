"""Render per-folder accent CSS variables for the web shell.

The strict CSP (``style-src 'self'``) forbids inline ``style=`` attributes, so
folder accents are published as a same-origin stylesheet mapping
``.folder-accent-<slug>`` to ``--folder-accent``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping

from rollup.folder_theme import FolderThemeOverride, folder_slug, theme_for

_SAFE_COLOUR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,24})$")
_SAFE_SLUG = re.compile(r"^[a-z0-9-]{1,80}$")


def _indexed_folders(conn: sqlite3.Connection | None) -> list[str]:
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT DISTINCT folder_name FROM rollup_entries WHERE folder_name IS NOT NULL LIMIT 500"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows if r[0]]


def render_theme_css(
    overrides: Mapping[str, FolderThemeOverride],
    conn: sqlite3.Connection | None = None,
) -> str:
    names: dict[str, str] = {}
    for slug in overrides:
        names.setdefault(folder_slug(slug), slug)
    for folder in _indexed_folders(conn):
        names.setdefault(folder_slug(folder), folder)
    lines = ["/* Rollup folder accents (generated, read-only) */"]
    for slug in sorted(names):
        if not _SAFE_SLUG.match(slug):
            continue
        accent = theme_for(names[slug], overrides).accent
        if not accent or not _SAFE_COLOUR.match(accent):
            continue
        lines.append(f".folder-accent-{slug} {{ --folder-accent: {accent}; }}")
    return "\n".join(lines) + "\n"
