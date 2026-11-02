"""HTTPS feed fetch with per-hop SSRF checks. No new dependencies."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests

from rollup.error_sanitize import sanitize_provider_message
from rollup.rss.models import RssItem
from rollup.rss.parse import RssParseError, parse_feed_xml, reject_dangerous_xml
from rollup.webpage.fetch import REQUEST_TIMEOUT
from rollup.webpage.url import (
    assert_safe_fetch_host,
    canonicalize_https_url,
    normalize_redirect_url,
)

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1_000_000
MAX_REDIRECTS = 5
USER_AGENT = "rollup-rss/0.12"


class RssFetchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"})
    return session


def fetch_feed(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    session: requests.Session | None = None,
) -> tuple[int, str | None, str | None, list[RssItem] | None]:
    """GET one feed.

    Returns ``(status, etag, last_modified, items)``. ``items`` is None on HTTP 304.
    """
    try:
        canonical = canonicalize_https_url(url)
    except ValueError as exc:
        raise RssFetchError("rss_url_invalid", str(exc)) from exc
    host = urlparse(canonical).hostname
    if host is None:
        raise RssFetchError("rss_url_invalid", "URL must include a host")
    try:
        assert_safe_fetch_host(host)
    except ValueError as exc:
        raise RssFetchError("rss_url_ssrf", str(exc)) from exc

    own = session is None
    sess = session or _session()
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        response = sess.get(
            canonical,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        hops = 0
        while response.is_redirect and hops < MAX_REDIRECTS:
            location = response.headers.get("Location")
            if not location:
                raise RssFetchError("rss_redirect_invalid", "Redirect without Location header")
            try:
                canonical = normalize_redirect_url(location)
            except ValueError as exc:
                raise RssFetchError("rss_url_ssrf", str(exc)) from exc
            response.close()
            response = sess.get(
                canonical,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            hops += 1
        status = response.status_code
        new_etag = response.headers.get("ETag")
        new_modified = response.headers.get("Last-Modified")
        if status == 304:
            return 304, new_etag or etag, new_modified or last_modified, None
        if status >= 400:
            raise RssFetchError("rss_fetch_failed", f"HTTP {status}")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise RssFetchError("rss_too_large", f"Feed exceeds {MAX_RESPONSE_BYTES} bytes")
            chunks.append(chunk)
        text = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    except RssFetchError:
        raise
    except requests.RequestException as exc:
        raise RssFetchError(
            "rss_fetch_failed", sanitize_provider_message(str(exc))
        ) from exc
    finally:
        if own:
            sess.close()
    try:
        reject_dangerous_xml(text)
        items = parse_feed_xml(text, feed_url=canonical)
    except RssParseError as exc:
        raise RssFetchError(exc.code, str(exc)) from exc
    return status, new_etag, new_modified, items
