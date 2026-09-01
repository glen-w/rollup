"""Fetch webpage articles with SSRF checks and size limits."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests
from urllib.parse import urlparse

from rollup.article_html import extract_article_text_from_html, extract_title_from_html
from rollup.error_sanitize import sanitize_provider_message
from rollup.webpage.config import BACKOFF_SECONDS, MAX_WEBPAGE_FETCHES
from rollup.webpage.url import assert_safe_fetch_host, canonicalize_https_url, normalize_redirect_url

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
MAX_BYTES = 2_000_000
USER_AGENT = "rollup/webpage"


@dataclass(frozen=True)
class WebpageFetchResult:
    url: str
    title: str
    body_text: str
    warnings: tuple[str, ...]


class WebpageFetchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _validate_redirect_url(url: str) -> str:
    try:
        return normalize_redirect_url(url)
    except ValueError as exc:
        raise WebpageFetchError("webpage_redirect_invalid", str(exc)) from exc


def fetch_webpage(url: str, session: requests.Session | None = None) -> WebpageFetchResult:
    """GET HTTPS article URL; extract title and body."""
    warnings: list[str] = []
    try:
        canonical = canonicalize_https_url(url)
    except ValueError as exc:
        raise WebpageFetchError("webpage_url_invalid", str(exc)) from exc

    host = urlparse(canonical).hostname
    if host is None:
        raise WebpageFetchError("webpage_url_invalid", "URL must include a host")
    try:
        assert_safe_fetch_host(host)
    except ValueError as exc:
        raise WebpageFetchError("webpage_url_ssrf", str(exc)) from exc

    own_session = session is None
    sess = session or _session()
    try:
        response = sess.get(
            canonical,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        # Follow redirects manually with SSRF re-check per hop.
        hops = 0
        while response.is_redirect and hops < 10:
            location = response.headers.get("Location")
            if not location:
                raise WebpageFetchError(
                    "webpage_redirect_invalid", "Redirect without Location header"
                )
            canonical = _validate_redirect_url(location)
            response.close()
            response = sess.get(
                canonical,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            hops += 1

        if response.status_code >= 400:
            raise WebpageFetchError(
                "webpage_fetch_failed",
                f"HTTP {response.status_code}",
            )

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BYTES:
                warnings.append("webpage_too_large")
                break
            chunks.append(chunk)

        encoding = response.encoding or "utf-8"
        html = b"".join(chunks).decode(encoding, errors="replace")
    except requests.RequestException as exc:
        logger.warning(
            "Webpage fetch failed for %s: %s",
            canonical[:80],
            sanitize_provider_message(str(exc)),
        )
        raise WebpageFetchError(
            "webpage_fetch_failed", sanitize_provider_message(str(exc))
        ) from exc
    finally:
        if own_session:
            sess.close()

    title = extract_title_from_html(html)
    body = extract_article_text_from_html(html)
    if not body:
        raise WebpageFetchError("webpage_empty", "No extractable article body")
    return WebpageFetchResult(
        url=canonical,
        title=title,
        body_text=body,
        warnings=tuple(warnings),
    )


def fetch_webpages_with_backoff(
    urls: list[str],
    *,
    max_fetches: int = MAX_WEBPAGE_FETCHES,
    session: requests.Session | None = None,
) -> list[tuple[str, WebpageFetchResult | WebpageFetchError]]:
    """Fetch URLs with per-run cap and backoff between requests."""
    results: list[tuple[str, WebpageFetchResult | WebpageFetchError]] = []
    own_session = session is None
    sess = session or _session()
    fetch_count = 0
    try:
        for url in urls:
            if fetch_count >= max_fetches:
                results.append(
                    (
                        url,
                        WebpageFetchError(
                            "webpage_fetch_cap",
                            "Per-run fetch cap reached",
                        ),
                    )
                )
                continue
            if fetch_count > 0:
                time.sleep(BACKOFF_SECONDS)
            fetch_count += 1
            try:
                results.append((url, fetch_webpage(url, sess)))
            except WebpageFetchError as exc:
                results.append((url, exc))
    finally:
        if own_session:
            sess.close()
    return results
