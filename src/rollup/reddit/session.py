"""Reddit listing fetch (public JSON/RSS ladder; optional OAuth)."""

from __future__ import annotations

import base64
import os
import time
from typing import Literal
from urllib.parse import urlencode

import requests

from rollup import __version__

REDDIT_WWW_BASE = "https://www.reddit.com"
REDDIT_OLD_BASE = "https://old.reddit.com"
REDDIT_OAUTH_BASE = "https://oauth.reddit.com"
RATE_LIMIT_RETRY_SECONDS = 60.0
RATE_LIMIT_MAX_RETRIES = 5
REQUEST_TIMEOUT = 30
MIN_SUB_FETCH_BACKOFF_SECONDS = 70.0

# Back-compat alias for tests/docs.
REDDIT_RSS_BASE = REDDIT_WWW_BASE


class RedditSessionError(RuntimeError):
    """Reddit listing fetch failed."""

    def __init__(self, message: str, *, retriable: bool = True) -> None:
        super().__init__(message)
        self.retriable = retriable


class RedditNotFoundError(RedditSessionError):
    """Subreddit or listing not found (do not retry)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=False)


def reddit_user_agent() -> str:
    return f"rollup/{__version__}"


def rss_sort_path(sort: str) -> str:
    """Map config sort to a listing path segment (rising/controversial → hot)."""
    if sort in ("hot", "new", "top"):
        return sort
    return "hot"


def build_rss_url(
    sub: str,
    sort: str,
    *,
    time_filter: str | None = None,
    base: str = REDDIT_WWW_BASE,
) -> str:
    segment = rss_sort_path(sort)
    url = f"{base}/r/{sub}/{segment}.rss"
    if segment == "top" and time_filter:
        url = f"{url}?{urlencode({'t': time_filter})}"
    return url


def build_json_url(
    sub: str,
    sort: str,
    *,
    time_filter: str | None = None,
    limit: int = 25,
    base: str = REDDIT_WWW_BASE,
) -> str:
    segment = rss_sort_path(sort)
    params: dict[str, str | int] = {"limit": min(max(1, limit), 100), "raw_json": 1}
    if segment == "top" and time_filter:
        params["t"] = time_filter
    return f"{base}/r/{sub}/{segment}.json?{urlencode(params)}"


def _ensure_listing_body(
    listing_format: Literal["json", "rss"],
    resp: requests.Response,
    *,
    label: str,
) -> str:
    """Reject HTML login walls masquerading as 200 OK."""
    content_type = resp.headers.get("Content-Type", "").lower()
    body = resp.text or ""
    stripped = body.lstrip()
    if listing_format == "json":
        if "json" not in content_type and not stripped.startswith("{"):
            raise RedditSessionError(
                format_http_error(resp, label=label),
                retriable=True,
            )
    elif not (
        "xml" in content_type
        or "rss" in content_type
        or "atom" in content_type
        or stripped.startswith("<?xml")
        or stripped.startswith("<feed")
    ):
        raise RedditSessionError(
            format_http_error(resp, label=label),
            retriable=True,
        )
    return body


def format_http_error(resp: requests.Response, *, label: str) -> str:
    """Human-readable fetch failure with status, content-type, and body snippet."""
    content_type = resp.headers.get("Content-Type", "")
    body = (resp.text or "").strip().replace("\n", " ")[:200]
    parts = [f"{label} failed ({resp.status_code})", f"content-type={content_type}"]
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        parts.append(f"retry-after={retry_after}")
    if body:
        parts.append(body)
    return ": ".join(parts)


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


def _request_headers(*, bearer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": reddit_user_agent(),
        "Connection": "close",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _oauth_credentials() -> tuple[str, str, str, str] | None:
    """Script-app credentials from env; None when OAuth tier should be skipped."""
    client_id = os.environ.get("ROLLUP_REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ROLLUP_REDDIT_CLIENT_SECRET", "").strip()
    username = os.environ.get("ROLLUP_REDDIT_USERNAME", "").strip()
    password = os.environ.get("ROLLUP_REDDIT_PASSWORD", "").strip()
    if not all((client_id, client_secret, username, password)):
        return None
    return client_id, client_secret, username, password


def _fetch_oauth_token(session: requests.Session) -> str:
    creds = _oauth_credentials()
    if creds is None:
        raise RedditSessionError("Reddit OAuth credentials incomplete")
    client_id, client_secret, username, password = creds
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = session.post(
        f"{REDDIT_WWW_BASE}/api/v1/access_token",
        headers={
            "User-Agent": reddit_user_agent(),
            "Authorization": f"Basic {auth}",
        },
        data={
            "grant_type": "password",
            "username": username,
            "password": password,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RedditSessionError(format_http_error(resp, label="Reddit OAuth token"))
    payload = resp.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RedditSessionError("Reddit OAuth token response missing access_token")
    return token


class RssRedditClient:
    """Fetch subreddit listings via a transport ladder (OAuth → JSON → RSS)."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session
        self.recommended_wait_seconds = MIN_SUB_FETCH_BACKOFF_SECONDS
        self._oauth_token: str | None = None

    def _http_session(self) -> requests.Session:
        return self._session or requests.Session()

    def _get_with_retry(
        self,
        url: str,
        *,
        label: str,
        headers: dict[str, str],
    ) -> requests.Response:
        session = self._http_session()
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            raise RedditNotFoundError(format_http_error(resp, label=label))
        for _attempt in range(RATE_LIMIT_MAX_RETRIES):
            if resp.status_code != 429:
                break
            time.sleep(_rate_limit_wait_seconds(resp))
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                raise RedditNotFoundError(format_http_error(resp, label=label))
        if resp.status_code != 200:
            raise RedditSessionError(format_http_error(resp, label=label))
        self.recommended_wait_seconds = _rate_limit_wait_seconds(resp)
        return resp

    def _oauth_token_cached(self, session: requests.Session) -> str | None:
        if _oauth_credentials() is None:
            return None
        if self._oauth_token is None:
            self._oauth_token = _fetch_oauth_token(session)
        return self._oauth_token

    def fetch_listing(
        self,
        sub: str,
        sort: str,
        *,
        time_filter: str | None = None,
        limit: int = 25,
    ) -> tuple[Literal["json", "rss"], str]:
        """Return (format, body) from the first successful transport."""
        segment = rss_sort_path(sort)
        errors: list[str] = []
        session = self._http_session()

        token = self._oauth_token_cached(session)
        if token:
            url = build_json_url(
                sub,
                sort,
                time_filter=time_filter,
                limit=limit,
                base=REDDIT_OAUTH_BASE,
            )
            label = f"Reddit OAuth JSON r/{sub}/{segment}"
            try:
                resp = self._get_with_retry(url, label=label, headers=_request_headers(bearer=token))
                return "json", _ensure_listing_body("json", resp, label=label)
            except RedditSessionError as exc:
                errors.append(str(exc))

        url = build_json_url(sub, sort, time_filter=time_filter, limit=limit)
        label = f"Reddit JSON r/{sub}/{segment}"
        try:
            resp = self._get_with_retry(url, label=label, headers=_request_headers())
            return "json", _ensure_listing_body("json", resp, label=label)
        except RedditSessionError as exc:
            errors.append(str(exc))

        url = build_rss_url(sub, sort, time_filter=time_filter)
        label = f"Reddit RSS r/{sub}/{segment}"
        try:
            resp = self._get_with_retry(url, label=label, headers=_request_headers())
            return "rss", _ensure_listing_body("rss", resp, label=label)
        except RedditSessionError as exc:
            errors.append(str(exc))

        url = build_rss_url(sub, sort, time_filter=time_filter, base=REDDIT_OLD_BASE)
        label = f"Reddit old RSS r/{sub}/{segment}"
        try:
            resp = self._get_with_retry(url, label=label, headers=_request_headers())
            return "rss", _ensure_listing_body("rss", resp, label=label)
        except RedditSessionError as exc:
            errors.append(str(exc))

        raise RedditSessionError("; ".join(errors))

    def fetch_feed(
        self,
        sub: str,
        sort: str,
        *,
        time_filter: str | None = None,
    ) -> str:
        """Fetch www RSS only (legacy; prefer ``fetch_listing``)."""
        segment = rss_sort_path(sort)
        url = build_rss_url(sub, sort, time_filter=time_filter)
        resp = self._get_with_retry(
            url,
            label=f"Reddit RSS r/{sub}/{segment}",
            headers=_request_headers(),
        )
        return _ensure_listing_body("rss", resp, label=f"Reddit RSS r/{sub}/{segment}")


def build_reddit_client() -> RssRedditClient:
    return RssRedditClient()
