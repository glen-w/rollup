"""Folder display themes: deterministic accents with optional overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

# Curated neutral palette — intentional without personal taxonomy branding.
ACCENT_PALETTE: tuple[str, ...] = (
    "#4a7fd4",  # blue
    "#4a9e6b",  # green
    "#c47a3a",  # amber
    "#8b7fa8",  # muted violet
    "#5a8a9e",  # teal
    "#a67c6d",  # warm taupe
    "#6b8f71",  # sage
    "#9a7b5f",  # brown
    "#7a8ba8",  # slate blue
    "#b07a7a",  # dusty rose
    "#6a9a8a",  # sea green
    "#8a8a6a",  # olive
)

DEFAULT_FOLDER_ACCENT = "#ccc"

_FOLDER_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class FolderTheme:
    emoji: str | None
    accent: str


@dataclass(frozen=True)
class FolderThemeOverride:
    emoji: str | None = None
    accent: str | None = None


def folder_slug(folder: str) -> str:
    slug = _FOLDER_SLUG_RE.sub("-", folder.lower()).strip("-")
    return slug or "folder"


def _stable_palette_index(slug: str) -> int:
    # FNV-1a 32-bit — stable across processes, no dependency on PYTHONHASHSEED.
    h = 2166136261
    for ch in slug.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return h % len(ACCENT_PALETTE)


def accent_for_slug(slug: str) -> str:
    return ACCENT_PALETTE[_stable_palette_index(slug)]


def theme_for(
    folder: str,
    overrides: Mapping[str, FolderThemeOverride] | None = None,
) -> FolderTheme:
    slug = folder_slug(folder)
    override = (overrides or {}).get(slug)
    if override is None and overrides:
        # Also allow lookup by raw lowercased folder name.
        override = overrides.get(folder.lower())
    emoji: str | None = None
    accent = accent_for_slug(slug)
    if override is not None:
        if override.emoji is not None:
            emoji = override.emoji or None
        if override.accent is not None:
            accent = override.accent
    return FolderTheme(emoji=emoji, accent=accent)


def folder_display_name(
    folder: str,
    overrides: Mapping[str, FolderThemeOverride] | None = None,
) -> str:
    theme = theme_for(folder, overrides)
    if theme.emoji:
        return f"{theme.emoji} {folder}"
    return folder


def folder_accent_css(
    folders: tuple[str, ...] | list[str],
    overrides: Mapping[str, FolderThemeOverride] | None = None,
) -> str:
    """CSS rules for folder accents that appear in the digest, plus default."""
    rules: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        slug = folder_slug(folder)
        if slug in seen:
            continue
        seen.add(slug)
        color = theme_for(folder, overrides).accent
        selector = f".folder-accent-{slug}"
        rules.append(
            f"{selector}>h2{{border-left:4px solid {color};padding-left:0.5rem;}}"
        )
        rules.append(
            f"{selector} .newsletter-card{{border-color:{color};border-left-width:3px;}}"
        )
        rules.append(
            f"{selector} .digest-group{{border-color:{color};border-left-width:3px;}}"
        )
    rules.append(
        f".folder-accent-default>h2{{border-left:4px solid {DEFAULT_FOLDER_ACCENT};padding-left:0.5rem;}}"
    )
    rules.append(
        f".folder-accent-default .newsletter-card{{border-color:{DEFAULT_FOLDER_ACCENT};border-left-width:3px;}}"
    )
    rules.append(
        f".folder-accent-default .digest-group{{border-color:{DEFAULT_FOLDER_ACCENT};border-left-width:3px;}}"
    )
    return "".join(rules)
