"""Fetch and extract linked article bodies for LinkedIn link posts."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import requests

from rollup.article_html import extract_article_text_from_html
from rollup.error_sanitize import sanitize_provider_message
from rollup.linkedin.models import LinkedInPost

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
MAX_BYTES = 2_000_000
MAX_ARTICLE_FETCHES = 50
BACKOFF_SECONDS = 1.0
ARTICLE_SEPARATOR = "\n\n---\n\n"


def _is_safe_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def fetch_article_text(url: str, session: requests.Session) -> tuple[str, list[str]]:
    """GET article URL and return extracted plain text plus warning codes."""
    warnings: list[str] = []
    if not _is_safe_https_url(url):
        return "", ["linkedin_article_url_invalid"]

    headers = {"User-Agent": session.headers.get("User-Agent", "rollup/linkedin")}
    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers=headers,
            allow_redirects=True,
            stream=True,
        )
    except requests.RequestException as exc:
        logger.warning(
            "LinkedIn article fetch failed for %s: %s",
            url[:80],
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
    text = extract_article_text_from_html(html)
    if not text:
        return "", warnings + ["linkedin_article_empty"]
    return text, warnings


def enrich_post_with_article(
    post: LinkedInPost,
    session: requests.Session,
    *,
    enabled: bool,
) -> tuple[LinkedInPost, tuple[str, ...]]:
    """Append fetched article body to commentary when enabled and URL is present."""
    if not enabled or not post.article_url:
        return post, ()
    article_text, warnings = fetch_article_text(post.article_url, session)
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
    session: requests.Session,
    *,
    enabled: bool,
) -> tuple[list[LinkedInPost], list[tuple[str, ...]]]:
    """Enrich posts with article bodies; cap fetches and apply backoff."""
    if not enabled:
        return posts, [() for _ in posts]

    enriched: list[LinkedInPost] = []
    per_post_warnings: list[tuple[str, ...]] = []
    fetch_count = 0

    for post in posts:
        if not post.article_url:
            enriched.append(post)
            per_post_warnings.append(())
            continue
        if fetch_count >= MAX_ARTICLE_FETCHES:
            enriched.append(post)
            per_post_warnings.append(("linkedin_article_fetch_cap",))
            continue
        if fetch_count > 0:
            time.sleep(BACKOFF_SECONDS)
        fetch_count += 1
        new_post, warnings = enrich_post_with_article(
            post, session, enabled=True
        )
        enriched.append(new_post)
        per_post_warnings.append(warnings)

    return enriched, per_post_warnings
