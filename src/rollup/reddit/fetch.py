"""Reddit public listing fetch and parse."""

from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Literal, Protocol

from rollup.reddit.config import (
    RedditConfig,
    RedditSub,
    lookback_to_time_filter,
)
from rollup.reddit.models import RedditPost
from rollup.reddit.session import (
    RedditSessionError,
    RssRedditClient,
    build_reddit_client,
    rss_sort_path,
)

logger = logging.getLogger(__name__)

# Unauthenticated Reddit RSS is ~1 request/minute; space subs accordingly.
SUB_FETCH_BACKOFF_SECONDS = 70.0
ATOM_NS = "http://www.w3.org/2005/Atom"


def reddit_fetch_wait_seconds(
    sub_count: int,
    *,
    backoff_seconds: float = SUB_FETCH_BACKOFF_SECONDS,
) -> float:
    """Happy-path sleep time: first sub is immediate, then *backoff* between the rest."""
    n = max(0, int(sub_count))
    if n <= 1:
        return 0.0
    return (n - 1) * float(backoff_seconds)


def format_reddit_duration(seconds: float) -> str:
    """Short human duration for CLI/GUI (``about 32 min``)."""
    if seconds <= 15:
        return "a few seconds"
    minutes = int(round(seconds / 60.0))
    if minutes < 1:
        return "under 1 min"
    if minutes == 1:
        return "about 1 min"
    if minutes < 60:
        return f"about {minutes} min"
    hours, mins = divmod(minutes, 60)
    if mins == 0:
        return "about 1 h" if hours == 1 else f"about {hours} h"
    return f"about {hours} h {mins} min"


def reddit_fetch_eta_phrase(
    sub_count: int,
    *,
    backoff_seconds: float = SUB_FETCH_BACKOFF_SECONDS,
) -> str | None:
    """ETA phrase when the planned wait is material; None for 0–1 subs."""
    wait = reddit_fetch_wait_seconds(sub_count, backoff_seconds=backoff_seconds)
    if wait <= 0:
        return None
    return format_reddit_duration(wait)


def reddit_fetch_eta_log_line(
    sub_count: int,
    *,
    backoff_seconds: float = SUB_FETCH_BACKOFF_SECONDS,
    prefix: str = "Fetching Reddit",
) -> str:
    n = max(0, int(sub_count))
    phrase = reddit_fetch_eta_phrase(n, backoff_seconds=backoff_seconds) or "a few seconds"
    backoff = int(backoff_seconds)
    noun = "sub" if n == 1 else "subs"
    return (
        f"{prefix}: {n} {noun}, {phrase} "
        f"({backoff}s between subs; 429s add extra wait)"
    )


class RedditFetchError(RuntimeError):
    """Reddit fetch failed (rate limit, parse)."""


class RedditClient(Protocol):
    def fetch_listing(
        self,
        sub: str,
        sort: str,
        *,
        time_filter: str | None = None,
        limit: int = 25,
    ) -> tuple[Literal["json", "rss"], str]:
        """Return (format, body) for a subreddit listing."""
        ...


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


def posts_from_json(json_text: str, *, subreddit: str) -> list[RedditPost]:
    """Parse Reddit listing JSON into RedditPost rows."""
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RedditFetchError(f"Invalid listing JSON: {exc}") from exc
    children = payload.get("data", {}).get("children", [])
    if not isinstance(children, list):
        raise RedditFetchError("Invalid listing JSON: missing data.children")

    sub = subreddit.strip().lower()
    posts: list[RedditPost] = []
    for child in children:
        if not isinstance(child, dict) or child.get("kind") != "t3":
            continue
        data = child.get("data")
        if not isinstance(data, dict):
            continue
        post_id = str(data.get("id") or "").strip()
        if not post_id:
            continue
        title = str(data.get("title") or "").strip()
        selftext = str(data.get("selftext") or "").strip()
        author = str(data.get("author") or "(unknown)").strip() or "(unknown)"
        permalink_raw = str(data.get("permalink") or "").strip()
        if permalink_raw.startswith("/"):
            permalink = f"https://www.reddit.com{permalink_raw}"
        else:
            permalink = permalink_raw
        url = str(data.get("url") or permalink).strip() or permalink
        created_raw = data.get("created_utc")
        created_at: datetime | None = None
        if isinstance(created_raw, (int, float)):
            created_at = datetime.fromtimestamp(float(created_raw), tz=timezone.utc)
        is_self = bool(data.get("is_self", bool(selftext)))
        posts.append(
            RedditPost(
                post_id=post_id,
                subreddit=sub,
                title=title,
                selftext=selftext if is_self else "",
                author=author,
                permalink=permalink,
                url=url,
                score=int(data.get("score") or 0),
                num_comments=int(data.get("num_comments") or 0),
                created_at=created_at,
                over_18=bool(data.get("over_18")),
                is_self=is_self,
            )
        )
    return posts


def _posts_from_listing(
    listing_format: Literal["json", "rss"],
    body: str,
    *,
    subreddit: str,
) -> list[RedditPost]:
    if listing_format == "json":
        return posts_from_json(body, subreddit=subreddit)
    return posts_from_rss(body, subreddit=subreddit)


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
    listing_format, body = client.fetch_listing(
        sub.name,
        sort,
        time_filter=time_filter,
        limit=limit,
    )
    posts = _posts_from_listing(listing_format, body, subreddit=sub.name)[:limit]
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
    """Test client backed by JSON/RSS fixture files."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._fixtures_dir = fixtures_dir

    def fetch_listing(
        self,
        sub: str,
        sort: str,
        *,
        time_filter: str | None = None,
        limit: int = 25,
    ) -> tuple[Literal["json", "rss"], str]:
        del time_filter, limit
        json_path = self._fixtures_dir / "listing_hot.json"
        if json_path.is_file():
            return "json", json_path.read_text(encoding="utf-8")
        name = "hot.rss" if rss_sort_path(sort) != "new" else "new.rss"
        fixture_path = self._fixtures_dir / name
        if not fixture_path.is_file():
            raise RedditFetchError(f"Fixture not found: {fixture_path}")
        return "rss", fixture_path.read_text(encoding="utf-8")


def fetch_posts_for_subs(
    subs: tuple[RedditSub, ...],
    *,
    config: RedditConfig,
    lookback_days: int,
    client: RedditClient | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[dict[str, list[RedditPost]], list[str]]:
    http = client or build_reddit_client()
    result: dict[str, list[RedditPost]] = {}
    failures: list[str] = []
    pending = sorted(subs, key=lambda s: s.name)
    if pending:
        logger.info("%s", reddit_fetch_eta_log_line(len(pending)))
    pass_index = 0
    while pending:
        if pass_index > 0:
            logger.info("Retrying %d Reddit subs after extra wait", len(pending))
            time.sleep(SUB_FETCH_BACKOFF_SECONDS * 2)
        next_pending: list[RedditSub] = []
        pass_total = len(pending)
        for index, sub in enumerate(pending):
            if index > 0:
                wait = SUB_FETCH_BACKOFF_SECONDS
                if isinstance(http, RssRedditClient):
                    wait = max(wait, http.recommended_wait_seconds)
                time.sleep(wait)
            remaining = reddit_fetch_wait_seconds(pass_total - index)
            remaining_phrase = format_reddit_duration(remaining) if remaining else None
            if remaining_phrase:
                logger.info(
                    "Reddit [%d/%d] r/%s (%s remaining)",
                    index + 1,
                    pass_total,
                    sub.name,
                    remaining_phrase,
                )
            else:
                logger.info("Reddit [%d/%d] r/%s", index + 1, pass_total, sub.name)
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
                message = f"r/{sub.name}: {exc}"
                retriable = getattr(exc, "retriable", True)
                if pass_index == 0 and retriable:
                    next_pending.append(sub)
                    logger.warning("Reddit fetch failed: %s", message)
                else:
                    failures.append(message)
                    logger.warning("Reddit fetch failed: %s", message)
        pending = next_pending
        pass_index += 1
    return result, failures
