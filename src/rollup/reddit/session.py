"""Reddit public RSS fetch client (no OAuth)."""

from __future__ import annotations

import time
from typing import Protocol
from urllib.parse import urlencode

import requests

from rollup import __version__

REDDIT_RSS_BASE = "https://www.reddit.com"
RATE_LIMIT_RETRY_SECONDS = 60.0
RATE_LIMIT_MAX_RETRIES = 5
REQUEST_TIMEOUT = 30
MIN_SUB_FETCH_BACKOFF_SECONDS = 70.0


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


def _rate_limit_wait_seconds(resp: requests.Response) -> float:
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return max(float(retry_after), RATE_LIMIT_RETRY_SECONDS)
        except (TypeError, ValueError):
            pass
    reset = resp.headers.get("x-ratelimit-reset")
    if reset:
        try:
            return max(float(reset) + 1.0, RATE_LIMIT_RETRY_SECONDS)
        except (TypeError, ValueError):
            pass
    return RATE_LIMIT_RETRY_SECONDS


class RssRedditClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self.recommended_wait_seconds = MIN_SUB_FETCH_BACKOFF_SECONDS

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
        for attempt in range(RATE_LIMIT_MAX_RETRIES):
            if resp.status_code != 429:
                break
            time.sleep(_rate_limit_wait_seconds(resp))
            resp = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise RedditSessionError(
                f"Reddit RSS r/{sub}/{rss_sort_path(sort)} failed "
                f"({resp.status_code}): {resp.text[:200]}"
            )
        self.recommended_wait_seconds = _rate_limit_wait_seconds(resp)
        return resp.text


def build_reddit_client() -> RssRedditClient:
    return RssRedditClient()
