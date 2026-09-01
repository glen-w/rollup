"""Reddit public RSS fetch and parse."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

from rollup.reddit.config import (
    RedditConfig,
    RedditSub,
    lookback_to_time_filter,
)
from rollup.reddit.models import RedditPost
from rollup.reddit.session import (
    RedditClient,
    RedditSessionError,
    build_reddit_client,
    rss_sort_path,
)

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = 1.0
ATOM_NS = "http://www.w3.org/2005/Atom"


class RedditFetchError(RuntimeError):
    """Reddit fetch failed (rate limit, parse)."""


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", unescape(raw))
    return " ".join(text.split())


def _parse_updated(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _post_id_from_entry_id(entry_id: str) -> str:
    """t3_abc123 or full URL → abc123."""
    if entry_id.startswith("t3_"):
        return entry_id[3:]
    match = re.search(r"/comments/([a-z0-9]+)/", entry_id, re.I)
    if match:
        return match.group(1)
    tail = entry_id.rsplit("/", 1)[-1]
    return tail if tail else entry_id


def _author_from_atom(entry: ET.Element) -> str:
    for author in entry.findall(f"{{{ATOM_NS}}}author"):
        name_el = author.find(f"{{{ATOM_NS}}}name")
        if name_el is not None and name_el.text:
            text = name_el.text.strip()
            if text.startswith("/u/"):
                return text[3:]
            return text.lstrip("/")
    return "(unknown)"


def _link_href(entry: ET.Element) -> str:
    for link in entry.findall(f"{{{ATOM_NS}}}link"):
        href = link.get("href")
        if href:
            return href
    link_id = entry.find(f"{{{ATOM_NS}}}id")
    if link_id is not None and link_id.text:
        return link_id.text.strip()
    return ""


def _entry_content(entry: ET.Element) -> str:
    for tag in ("content", "summary"):
        el = entry.find(f"{{{ATOM_NS}}}{tag}")
        if el is not None:
            if el.text and el.text.strip():
                return _strip_html(el.text)
            if el.tail and el.tail.strip():
                return _strip_html(el.tail)
            inner = "".join(ET.tostring(el, encoding="unicode", method="xml"))
            return _strip_html(inner)
    return ""


def posts_from_rss(xml_text: str, *, subreddit: str) -> list[RedditPost]:
    """Parse Reddit Atom feed XML into RedditPost rows."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RedditFetchError(f"Invalid RSS XML: {exc}") from exc

    sub = subreddit.strip().lower()
    posts: list[RedditPost] = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        entry_id_el = entry.find(f"{{{ATOM_NS}}}id")
        title_el = entry.find(f"{{{ATOM_NS}}}title")
        updated_el = entry.find(f"{{{ATOM_NS}}}updated")
        if entry_id_el is None or not entry_id_el.text:
            continue
        post_id = _post_id_from_entry_id(entry_id_el.text.strip())
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        permalink = _link_href(entry)
        content = _entry_content(entry)
        author = _author_from_atom(entry)
        is_self = bool(content.strip())
        posts.append(
            RedditPost(
                post_id=post_id,
                subreddit=sub,
                title=title,
                selftext=content if is_self else "",
                author=author,
                permalink=permalink,
                url=permalink,
                score=0,
                num_comments=0,
                created_at=_parse_updated(
                    updated_el.text if updated_el is not None else None
                ),
                over_18=False,
                is_self=is_self,
            )
        )
    return posts


def fetch_sub_posts(
    sub: RedditSub,
    *,
    config: RedditConfig,
    lookback_days: int,
    client: RedditClient,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[RedditPost]:
    sort = sub.resolved_sort(config.sort)
    limit = sub.resolved_limit(config.limit)
    time_filter = None
    if rss_sort_path(sort) == "top":
        time_filter = config.time_filter or lookback_to_time_filter(lookback_days)
    xml_text = client.fetch_feed(sub.name, sort, time_filter=time_filter)
    posts = posts_from_rss(xml_text, subreddit=sub.name)[:limit]
    if window_start is not None and window_end is not None:
        filtered: list[RedditPost] = []
        for post in posts:
            if post.created_at is None:
                continue
            if window_start <= post.created_at <= window_end:
                filtered.append(post)
        return filtered
    return posts


class FixtureRedditClient:
    """Test client backed by RSS fixture files."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._fixtures_dir = fixtures_dir

    def fetch_feed(
        self,
        sub: str,
        sort: str,
        *,
        time_filter: str | None = None,
    ) -> str:
        name = "hot.rss" if rss_sort_path(sort) != "new" else "new.rss"
        fixture_path = self._fixtures_dir / name
        if not fixture_path.is_file():
            raise RedditFetchError(f"Fixture not found: {fixture_path}")
        return fixture_path.read_text(encoding="utf-8")


def fetch_posts_for_subs(
    subs: tuple[RedditSub, ...],
    *,
    config: RedditConfig,
    lookback_days: int,
    client: RedditClient | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, list[RedditPost]]:
    http = client or build_reddit_client()
    result: dict[str, list[RedditPost]] = {}
    for sub in subs:
        try:
            posts = fetch_sub_posts(
                sub,
                config=config,
                lookback_days=lookback_days,
                client=http,
                window_start=window_start,
                window_end=window_end,
            )
            result[sub.name] = posts
        except (RedditSessionError, RedditFetchError) as exc:
            raise RedditFetchError(f"r/{sub.name}: {exc}") from exc
        time.sleep(BACKOFF_SECONDS)
    return result
