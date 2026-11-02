"""Title and URL normalization for coverage clustering."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rollup.attention.profile import tokenize
from rollup.links import TRACKING_QUERY_PARAMS, prepare_links_for_render
from rollup.models import DigestEntry, LinkItem

_DATE = re.compile(r"\b(?:20\d{2}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+\d{1,2})?(?:,?\s*20\d{2})?\b", re.I)
_ISSUE = re.compile(r"\b(?:issue|edition|#)\s*\d+\b", re.I)
_NOISE = re.compile(r"\b(?:newsletter|daily|weekly|roundup|digest)\b", re.I)


def normalize_title(subject: str) -> str:
    text = subject or ""
    text = _DATE.sub(" ", text)
    text = _ISSUE.sub(" ", text)
    text = _NOISE.sub(" ", text)
    return " ".join(text.lower().split())


def title_tokens(subject: str) -> frozenset[str]:
    return tokenize(normalize_title(subject))


def canonical_url(href: str) -> str | None:
    raw = (href or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_QUERY_PARAMS
    ]
    path = parsed.path.rstrip("/") or "/"
    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        query=urlencode(query),
        fragment="",
    )
    return urlunparse(cleaned)


def entry_main_urls(entry: DigestEntry, *, max_display_links: int = 8) -> tuple[str, ...]:
    parsed = entry.classified.parsed
    link_items = (
        list(parsed.link_items)
        if getattr(parsed, "link_items", ())
        else [
            LinkItem(href=href, text=None, context=None, source_index=i)
            for i, href in enumerate(parsed.links)
        ]
    )
    if not link_items and parsed.links:
        urls = [canonical_url(href) for href in parsed.links]
        return tuple(u for u in urls if u)
    max_main = min(5, max_display_links)
    bundle = prepare_links_for_render(
        link_items, max_main=max_main, max_other=0
    )
    urls = [canonical_url(link.href) for link in bundle.main_links]
    return tuple(u for u in urls if u)
