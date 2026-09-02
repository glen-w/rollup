"""Fetch and extract linked article bodies for LinkedIn link posts."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

from rollup.article_html import extract_article_text_from_html
from rollup.error_sanitize import sanitize_provider_message
from rollup.linkedin.models import LinkedInPost
from rollup.webpage.url import assert_safe_fetch_host, canonicalize_https_url, normalize_redirect_url

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
MAX_BYTES = 2_000_000
MAX_ARTICLE_FETCHES = 50
MAX_REDIRECTS = 10
BACKOFF_SECONDS = 1.0
ARTICLE_SEPARATOR = "\n\n---\n\n"
USER_AGENT = "rollup/linkedin"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _article_session() -> requests.Session:
    """Cookie-free session: never reuse the LinkedIn Voyager jar for third-party URLs."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _canonicalize_article_url(url: str) -> str:
    """Require https; raise ValueError on invalid or SSRF-blocked hosts."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("URL must not be empty")
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Only https article URLs are supported")
    canonical = canonicalize_https_url(cleaned)
    host = urlparse(canonical).hostname
    if host is None:
        raise ValueError("URL must include a host")
    assert_safe_fetch_host(host)
    return canonical


def fetch_article_text(
    url: str, session: requests.Session | None = None
) -> tuple[str, list[str]]:
    """GET article URL with SSRF/redirect checks; never follow into private nets.

    ``session`` is optional and must not carry LinkedIn cookies. When omitted a
    clean session is created. Returns extracted plain text plus warning codes.
    """
    warnings: list[str] = []
    try:
        canonical = _canonicalize_article_url(url)
    except ValueError as exc:
        msg = str(exc).lower()
        if "blocked" in msg or "cannot resolve" in msg:
            return "", ["linkedin_article_url_ssrf"]
        return "", ["linkedin_article_url_invalid"]

    own_session = session is None
    sess = session or _article_session()
    try:
        try:
            response = sess.get(
                canonical,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            hops = 0
            while response.status_code in _REDIRECT_STATUSES and hops < MAX_REDIRECTS:
                location = response.headers.get("Location")
                if not location:
                    return "", ["linkedin_article_url_invalid"]
                try:
                    canonical = normalize_redirect_url(location)
                except ValueError as exc:
                    msg = str(exc).lower()
                    if "blocked" in msg or "cannot resolve" in msg:
                        return "", ["linkedin_article_url_ssrf"]
                    return "", ["linkedin_article_url_invalid"]
                response.close()
                response = sess.get(
                    canonical,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                )
                hops += 1
        except requests.RequestException as exc:
            logger.warning(
                "LinkedIn article fetch failed for %s: %s",
                canonical[:80],
                sanitize_provider_message(str(exc)),
            )
            return "", ["linkedin_article_fetch_failed"]

        if response.status_code >= 400:
            return "", ["linkedin_article_fetch_failed"]

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BYTES:
                warnings.append("linkedin_article_too_large")
                break
            chunks.append(chunk)

        encoding = response.encoding or "utf-8"
        html = b"".join(chunks).decode(encoding, errors="replace")
    finally:
        if own_session:
            sess.close()

    text = extract_article_text_from_html(html)
    if not text:
        return "", warnings + ["linkedin_article_empty"]
    return text, warnings


def enrich_post_with_article(
    post: LinkedInPost,
    session: requests.Session | None = None,
    *,
    enabled: bool,
    conn: sqlite3.Connection | None = None,
    fetched_at: datetime | None = None,
) -> tuple[LinkedInPost, tuple[str, ...]]:
    """Append fetched article body to commentary when enabled and URL is present."""
    if not enabled or not post.article_url:
        return post, ()
    article_text = ""
    warnings: list[str] = []
    if conn is not None and post.article_url:
        from rollup.linkedin.cache import get_article_body, store_article_body

        cached = get_article_body(conn, post.article_url)
        if cached:
            article_text = cached
    if not article_text:
        article_text, warnings = fetch_article_text(post.article_url, session)
        if article_text and conn is not None and fetched_at is not None:
            from rollup.linkedin.cache import store_article_body

            store_article_body(
                conn,
                post.article_url,
                article_text,
                fetched_at=fetched_at,
            )
    if not article_text:
        return post, tuple(warnings)

    commentary = post.text.strip()
    if commentary:
        combined = f"{commentary}{ARTICLE_SEPARATOR}{article_text}"
    else:
        combined = article_text

    enriched = LinkedInPost(
        activity_id=post.activity_id,
        author_name=post.author_name,
        author_member_id=post.author_member_id,
        text=combined,
        permalink=post.permalink,
        created_at=post.created_at,
        article_url=post.article_url,
        article_title=post.article_title,
    )
    return enriched, tuple(warnings)


def enrich_posts_with_articles(
    posts: list[LinkedInPost],
    session: requests.Session | None = None,
    *,
    enabled: bool,
    conn: sqlite3.Connection | None = None,
    fetched_at: datetime | None = None,
) -> tuple[list[LinkedInPost], list[tuple[str, ...]]]:
    """Enrich posts with article bodies; cap fetches and apply backoff.

    Uses a cookie-free session for third-party article URLs. ``session`` is only
    for tests that inject a transport; production callers should omit it.
    """
    if not enabled:
        return posts, [() for _ in posts]

    own_session = session is None
    sess = session or _article_session()
    enriched: list[LinkedInPost] = []
    per_post_warnings: list[tuple[str, ...]] = []
    fetch_count = 0

    try:
        for post in posts:
            if not post.article_url:
                enriched.append(post)
                per_post_warnings.append(())
                continue
            needs_network = True
            if conn is not None:
                from rollup.linkedin.cache import get_article_body

                needs_network = get_article_body(conn, post.article_url) is None
            if needs_network and fetch_count >= MAX_ARTICLE_FETCHES:
                enriched.append(post)
                per_post_warnings.append(("linkedin_article_fetch_cap",))
                continue
            if needs_network and fetch_count > 0:
                time.sleep(BACKOFF_SECONDS)
            if needs_network:
                fetch_count += 1
            new_post, warnings = enrich_post_with_article(
                post,
                sess,
                enabled=True,
                conn=conn,
                fetched_at=fetched_at,
            )
            enriched.append(new_post)
            per_post_warnings.append(warnings)
    finally:
        if own_session:
            sess.close()

    return enriched, per_post_warnings
