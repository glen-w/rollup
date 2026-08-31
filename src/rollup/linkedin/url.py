"""LinkedIn content-search URL validation and lookback mapping."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ALLOWED_HOSTS = frozenset({"linkedin.com", "www.linkedin.com"})
CONTENT_SEARCH_PATH = "/search/results/content/"


class LinkedInUrlError(ValueError):
    """Invalid LinkedIn search URL."""


def validate_content_search_url(
    url: str,
    *,
    path: Path | None = None,
    context: str = "",
) -> str:
    """Validate and return a normalized content-search URL."""
    parsed = urlparse(url.strip())
    prefix = f"{path}: " if path else ""
    ctx = f" ({context})" if context else ""
    if parsed.scheme != "https":
        raise LinkedInUrlError(f"{prefix}LinkedIn search URL{ctx} must use https")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise LinkedInUrlError(
            f"{prefix}LinkedIn search URL{ctx} host must be linkedin.com or www.linkedin.com"
        )
    if not parsed.path.rstrip("/").endswith(CONTENT_SEARCH_PATH.rstrip("/")):
        raise LinkedInUrlError(
            f"{prefix}LinkedIn search URL{ctx} must be a content search "
            f"({CONTENT_SEARCH_PATH})"
        )
    return url.strip()


def lookback_to_date_posted(lookback_days: int) -> str:
    """Map Rollup lookback_days to LinkedIn datePosted facet value."""
    if lookback_days <= 1:
        return "past-24h"
    if lookback_days <= 7:
        return "past-week"
    if lookback_days <= 30:
        return "past-month"
    return "past-year"


def from_member_ids(url: str) -> tuple[str, ...]:
    """Extract fromMember facet tokens (ACo…) from a content-search URL."""
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    raw_values = query.get("fromMember") or []
    ids: list[str] = []
    for item in raw_values:
        text = item.strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            ids.append(text)
            continue
        if isinstance(decoded, list):
            ids.extend(str(v).strip() for v in decoded if str(v).strip())
        elif decoded:
            ids.append(str(decoded).strip())
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for member_id in ids:
        if member_id in seen:
            continue
        seen.add(member_id)
        out.append(member_id)
    return tuple(out)


def apply_lookback_to_url(url: str, lookback_days: int) -> str:
    """Return URL with datePosted facet adjusted for lookback_days."""
    validate_content_search_url(url)
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["datePosted"] = [json.dumps([lookback_to_date_posted(lookback_days)])]
    new_query = urlencode(query, doseq=True)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )
