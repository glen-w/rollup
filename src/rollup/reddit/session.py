"""Reddit public RSS fetch client (no OAuth)."""

from __future__ import annotations

import time
from typing import Protocol
from urllib.parse import urlencode

import requests

from rollup import __version__

REDDIT_RSS_BASE = "https://www.reddit.com"
RATE_LIMIT_RETRY_SECONDS = 60.0
REQUEST_TIMEOUT = 30


class RedditSessionError(RuntimeError):
    """Reddit RSS fetch failed."""


def reddit_user_agent() -> str:
    return f"rollup/{__version__}"


def rss_sort_path(sort: str) -> str:
    """Map config sort to an RSS path segment (rising/controversial → hot)."""
    if sort in ("hot", "new", "top"):
        return sort
    return "hot"


def build_rss_url(
    sub: str,
    sort: str,
    *,
    time_filter: str | None = None,
) -> str:
    segment = rss_sort_path(sort)
    url = f"{REDDIT_RSS_BASE}/r/{sub}/{segment}.rss"
    if segment == "top" and time_filter:
        url = f"{url}?{urlencode({'t': time_filter})}"
    return url


class RedditClient(Protocol):
    def fetch_feed(
        self,
        sub: str,
        sort: str,
        *,
        time_filter: str | None = None,
    ) -> str:
        """Return Atom/RSS XML for a subreddit listing."""
        ...


class RssRedditClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def fetch_feed(
        self,
        sub: str,
        sort: str,
        *,
        time_filter: str | None = None,
    ) -> str:
        url = build_rss_url(sub, sort, time_filter=time_filter)
        headers = {"User-Agent": reddit_user_agent()}
        resp = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            time.sleep(RATE_LIMIT_RETRY_SECONDS)
            resp = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise RedditSessionError(
                f"Reddit RSS r/{sub}/{rss_sort_path(sort)} failed "
                f"({resp.status_code}): {resp.text[:200]}"
            )
        return resp.text


def build_reddit_client() -> RssRedditClient:
    return RssRedditClient()
