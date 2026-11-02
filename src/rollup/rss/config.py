"""RSS / Atom ingest configuration. Opt-in transport, not an inbox."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from rollup.source_fetch_cache import DEFAULT_FETCH_TTL_HOURS, parse_fetch_ttl_hours
from rollup.webpage.url import canonicalize_https_url

RssLayout = Literal["feed", "per_source"]
RSS_LAYOUTS = frozenset({"feed", "per_source"})
RSS_FOLDER_PREFIX = "rss:"
RSS_FEED_FOLDER = "rss:feed"
MAX_RSS_FEEDS = 40
RSS_TOP_KEYS = frozenset({"enabled", "layout", "fetch_ttl_hours", "feeds"})
FEED_KEYS = frozenset({"url", "enabled", "display_name"})


@dataclass(frozen=True)
class RssFeed:
    slug: str
    url: str
    enabled: bool = True
    display_name: str | None = None


@dataclass(frozen=True)
class RssConfig:
    enabled: bool = False
    layout: RssLayout = "feed"
    fetch_ttl_hours: int = DEFAULT_FETCH_TTL_HOURS
    feeds: dict[str, RssFeed] = field(default_factory=dict)


def folder_name_for_feed(feed: RssFeed, *, layout: RssLayout) -> str:
    if layout == "feed":
        return RSS_FEED_FOLDER
    host = (urlparse(feed.url).hostname or feed.slug).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-")[:40] or "feed"
    return f"{RSS_FOLDER_PREFIX}{slug}"


def slug_for_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "feed").lower()
    path = parsed.path.strip("/").replace("/", "-")
    raw = f"{host}-{path}" if path else host
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:60]
    return slug or "feed"


def parse_rss_config(raw: object, *, path: Path) -> RssConfig:
    if raw is None:
        return RssConfig()
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: [rss] must be a table")
    unknown = set(raw) - RSS_TOP_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"{path}: unknown key(s) in [rss]: {keys}")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"{path}: [rss].enabled must be a boolean")
    layout = raw.get("layout", "feed")
    if layout not in RSS_LAYOUTS:
        raise ValueError(f"{path}: [rss].layout must be feed or per_source")
    ttl = parse_fetch_ttl_hours(
        raw.get("fetch_ttl_hours", DEFAULT_FETCH_TTL_HOURS),
        path=str(path),
        context="[rss].fetch_ttl_hours",
    )
    feeds_raw = raw.get("feeds") or {}
    if not isinstance(feeds_raw, Mapping):
        raise ValueError(f"{path}: [rss].feeds must be a table")
    feeds: dict[str, RssFeed] = {}
    for slug, body in feeds_raw.items():
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError(f"{path}: [rss].feeds keys must be non-empty strings")
        if not isinstance(body, Mapping):
            raise ValueError(f"{path}: [rss.feeds.{slug}] must be a table")
        extra = set(body) - FEED_KEYS
        if extra:
            keys = ", ".join(sorted(str(k) for k in extra))
            raise ValueError(f"{path}: unknown key(s) in [rss.feeds.{slug}]: {keys}")
        url_raw = body.get("url")
        if not isinstance(url_raw, str) or not url_raw.strip():
            raise ValueError(f"{path}: [rss.feeds.{slug}].url must be a string")
        try:
            url = canonicalize_https_url(url_raw)
        except ValueError as exc:
            raise ValueError(f"{path}: [rss.feeds.{slug}].url: {exc}") from exc
        feed_enabled = body.get("enabled", True)
        if not isinstance(feed_enabled, bool):
            raise ValueError(f"{path}: [rss.feeds.{slug}].enabled must be a boolean")
        display = body.get("display_name")
        if display is not None and not isinstance(display, str):
            raise ValueError(f"{path}: [rss.feeds.{slug}].display_name must be a string")
        feeds[slug.strip()] = RssFeed(
            slug=slug.strip(),
            url=url,
            enabled=feed_enabled,
            display_name=display.strip() if isinstance(display, str) and display.strip() else None,
        )
    if len(feeds) > MAX_RSS_FEEDS:
        raise ValueError(f"{path}: [rss] supports at most {MAX_RSS_FEEDS} feeds")
    return RssConfig(
        enabled=enabled,
        layout=layout,
        fetch_ttl_hours=ttl,
        feeds=feeds,
    )


def filter_rss_feeds(
    config: RssConfig,
    *,
    folders_include: tuple[str, ...],
    folders_exclude: tuple[str, ...],
) -> tuple[RssFeed, ...]:
    result = [feed for feed in config.feeds.values() if feed.enabled]
    if folders_include:
        include_set = set(folders_include)
        filtered: list[RssFeed] = []
        for feed in result:
            folder = folder_name_for_feed(feed, layout=config.layout)
            if folder in include_set:
                filtered.append(feed)
            elif config.layout == "feed" and RSS_FEED_FOLDER in include_set:
                filtered.append(feed)
        result = filtered
    if folders_exclude:
        exclude_set = set(folders_exclude)
        result = [
            feed
            for feed in result
            if folder_name_for_feed(feed, layout=config.layout) not in exclude_set
            and not (config.layout == "feed" and RSS_FEED_FOLDER in exclude_set)
        ]
    return tuple(sorted(result[:MAX_RSS_FEEDS], key=lambda feed: feed.slug))


def list_rss_folder_names(
    rss_config: RssConfig | None,
    *,
    include: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
) -> list[str]:
    if rss_config is None or not rss_config.enabled:
        return []
    feeds = filter_rss_feeds(
        rss_config,
        folders_include=tuple(include),
        folders_exclude=tuple(exclude),
    )
    if not feeds:
        return []
    if rss_config.layout == "feed":
        return [RSS_FEED_FOLDER]
    seen: list[str] = []
    for feed in feeds:
        name = folder_name_for_feed(feed, layout="per_source")
        if name not in seen:
            seen.append(name)
    return seen
