"""LinkedIn configuration models and TOML parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from rollup.folder_theme import FolderThemeOverride, folder_slug
from rollup.linkedin.url import validate_content_search_url

LINKEDIN_FOLDER_PREFIX = "linkedin:"
LINKEDIN_FEED_FOLDER = "linkedin:feed"

LinkedInLayout = Literal["feed", "per_source", "per_search"]
LINKEDIN_LAYOUTS = frozenset({"feed", "per_source", "per_search"})

SEARCH_KEYS = frozenset({"url", "display_name", "enabled", "emoji", "accent", "order"})
MAX_LINKEDIN_SEARCHES = 50
_SEARCH_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class LinkedInSearch:
    slug: str
    url: str
    display_name: str | None = None
    enabled: bool = True
    emoji: str | None = None
    accent: str | None = None
    order: int | None = None

    @property
    def folder_name(self) -> str:
        return folder_name_for_search(self.slug)


def folder_name_for_search(slug: str) -> str:
    return f"{LINKEDIN_FOLDER_PREFIX}{slug.strip().lower()}"


def search_slug_from_name(name: str) -> str:
    """Derive a TOML/folder slug from a display name (e.g. BBNJ → bbnj)."""
    return _SEARCH_SLUG_RE.sub("-", name.strip().lower()).strip("-")


def linkedin_folder_theme_overrides(
    linkedin: LinkedInConfig,
) -> dict[str, FolderThemeOverride]:
    """Folder-theme overlays so named searches appear as digest section titles.

    Applied for ``per_search`` layout (each search is ``linkedin:<slug>``).
    Explicit ``[folders.*]`` entries still win when merged.
    """
    if linkedin.layout != "per_search":
        return {}
    out: dict[str, FolderThemeOverride] = {}
    for search in linkedin.searches.values():
        if not search.enabled:
            continue
        if not any(
            (
                search.display_name,
                search.emoji,
                search.accent,
                search.order is not None,
            )
        ):
            continue
        out[folder_slug(search.folder_name)] = FolderThemeOverride(
            emoji=search.emoji,
            accent=search.accent,
            display_name=search.display_name,
            order=search.order,
        )
    return out


def merge_linkedin_folder_themes(
    folder_themes: Mapping[str, FolderThemeOverride],
    linkedin: LinkedInConfig,
) -> dict[str, FolderThemeOverride]:
    """Overlay LinkedIn search names onto folder themes; ``[folders.*]`` wins."""
    merged = dict(folder_themes)
    for slug, overlay in linkedin_folder_theme_overrides(linkedin).items():
        existing = merged.get(slug)
        if existing is None:
            merged[slug] = overlay
            continue
        merged[slug] = FolderThemeOverride(
            emoji=existing.emoji if existing.emoji is not None else overlay.emoji,
            accent=existing.accent if existing.accent is not None else overlay.accent,
            display_name=(
                existing.display_name
                if existing.display_name is not None
                else overlay.display_name
            ),
            order=existing.order if existing.order is not None else overlay.order,
        )
    return merged


def linkedin_folder_for_post(
    *,
    search_slug: str,
    layout: str,
    author_member_id: str | None,
) -> str:
    if layout == "per_search":
        return folder_name_for_search(search_slug)
    if layout == "per_source" and author_member_id:
        safe = author_member_id.strip().lower()
        safe = "".join(c for c in safe if c.isalnum() or c in "_-")
        if safe:
            return f"{LINKEDIN_FOLDER_PREFIX}{safe[:80]}"
    return LINKEDIN_FEED_FOLDER


def slug_from_folder_name(folder_name: str) -> str | None:
    if not folder_name.startswith(LINKEDIN_FOLDER_PREFIX):
        return None
    slug = folder_name[len(LINKEDIN_FOLDER_PREFIX) :].strip()
    return slug or None


@dataclass(frozen=True)
class LinkedInConfig:
    enabled: bool = False
    article_fetch: bool = True
    layout: LinkedInLayout = "feed"
    searches: dict[str, LinkedInSearch] = field(default_factory=dict)


def parse_linkedin_config(raw: object | None, *, path: Path) -> LinkedInConfig:
    if raw is None:
        return LinkedInConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [linkedin] must be a table")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"{path}: [linkedin].enabled must be a boolean")

    article_fetch = raw.get("article_fetch", True)
    if not isinstance(article_fetch, bool):
        raise ValueError(f"{path}: [linkedin].article_fetch must be a boolean")

    layout = raw.get("layout", "feed")
    if layout not in LINKEDIN_LAYOUTS:
        raise ValueError(f"{path}: [linkedin].layout must be feed, per_source, or per_search")

    searches_raw = raw.get("searches")
    searches: dict[str, LinkedInSearch] = {}
    if searches_raw is not None:
        if not isinstance(searches_raw, dict):
            raise ValueError(f"{path}: [linkedin.searches] must be a table")
        for slug_raw, body in searches_raw.items():
            if not isinstance(slug_raw, str) or not slug_raw.strip():
                raise ValueError(
                    f"{path}: [linkedin.searches] keys must be non-empty strings"
                )
            slug = slug_raw.strip().lower()
            if not isinstance(body, dict):
                raise ValueError(f"{path}: [linkedin.searches.{slug}] must be a table")
            unknown = set(body) - SEARCH_KEYS
            if unknown:
                keys = ", ".join(sorted(unknown))
                raise ValueError(
                    f"{path}: unknown key(s) in [linkedin.searches.{slug}]: {keys}"
                )
            url = body.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError(
                    f"{path}: [linkedin.searches.{slug}].url must be a non-empty string"
                )
            validate_content_search_url(url.strip(), path=path, context=slug)
            display_name = body.get("display_name")
            if display_name is not None and not isinstance(display_name, str):
                raise ValueError(
                    f"{path}: [linkedin.searches.{slug}].display_name must be a string"
                )
            search_enabled = body.get("enabled", True)
            if not isinstance(search_enabled, bool):
                raise ValueError(
                    f"{path}: [linkedin.searches.{slug}].enabled must be a boolean"
                )
            emoji = body.get("emoji")
            accent = body.get("accent")
            order = body.get("order")
            if emoji is not None and not isinstance(emoji, str):
                raise ValueError(
                    f"{path}: [linkedin.searches.{slug}].emoji must be a string"
                )
            if accent is not None and not isinstance(accent, str):
                raise ValueError(
                    f"{path}: [linkedin.searches.{slug}].accent must be a string"
                )
            if order is not None and (not isinstance(order, int) or isinstance(order, bool)):
                raise ValueError(
                    f"{path}: [linkedin.searches.{slug}].order must be an integer"
                )
            searches[slug] = LinkedInSearch(
                slug=slug,
                url=url.strip(),
                display_name=display_name.strip() if display_name else None,
                enabled=search_enabled,
                emoji=emoji,
                accent=accent,
                order=order,
            )
            if len(searches) > MAX_LINKEDIN_SEARCHES:
                raise ValueError(
                    f"{path}: [linkedin.searches] supports at most "
                    f"{MAX_LINKEDIN_SEARCHES} saved searches"
                )

    return LinkedInConfig(
        enabled=enabled,
        article_fetch=article_fetch,
        layout=layout,  # type: ignore[arg-type]
        searches=searches,
    )


def filter_linkedin_searches(
    searches: dict[str, LinkedInSearch],
    *,
    folders_include: tuple[str, ...],
    folders_exclude: tuple[str, ...],
    layout: str = "feed",
) -> tuple[LinkedInSearch, ...]:
    """Return enabled searches after folder include/exclude filters."""
    result = [s for s in searches.values() if s.enabled]
    if folders_include:
        include_set = set(folders_include)
        if layout == "feed":
            if LINKEDIN_FEED_FOLDER not in include_set:
                result = [s for s in result if s.folder_name in include_set]
        else:
            result = [s for s in result if s.folder_name in include_set]
    if folders_exclude:
        exclude_set = set(folders_exclude)
        if layout == "feed" and LINKEDIN_FEED_FOLDER in exclude_set:
            result = []
        else:
            result = [s for s in result if s.folder_name not in exclude_set]
    return tuple(sorted(result, key=lambda s: s.slug))
