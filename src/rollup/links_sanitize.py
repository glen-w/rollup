"""URL and links_json sanitisation for indexing and web rendering."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from rollup.payload_limits import (
    LINKS_JSON_VERSION,
    MAX_LINK_HREF_LEN,
    MAX_LINK_ITEMS,
    MAX_LINK_LABEL_LEN,
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class LinkSanitizeError(ValueError):
    """Malformed or unsafe link payload."""


def sanitize_http_url(url: str | None, *, max_len: int = MAX_LINK_HREF_LEN) -> str | None:
    """Return a safe absolute http(s) URL or None if rejected."""
    if url is None:
        return None
    text = str(url).strip()
    if not text or len(text) > max_len:
        return None
    lower = text.lower()
    if lower.startswith("javascript:") or lower.startswith("data:"):
        return None
    if text.startswith("//"):
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return None
    if not parsed.netloc:
        return None
    # Reject credentials in netloc for safety.
    if "@" in parsed.netloc:
        return None
    return text


def build_links_json(items: list[tuple[str, str | None]]) -> str:
    """Build versioned links_json from (href, label) pairs; skips invalid hrefs."""
    out: list[dict[str, str]] = []
    for href, label in items[:MAX_LINK_ITEMS]:
        safe = sanitize_http_url(href)
        if safe is None:
            continue
        lab = (label or "").strip()[:MAX_LINK_LABEL_LEN]
        out.append({"href": safe, "label": lab})
    return json.dumps({"v": LINKS_JSON_VERSION, "items": out}, separators=(",", ":"))


def parse_links_json(raw: str | None) -> list[dict[str, str]]:
    """Decode and re-validate links_json; returns only safe items."""
    if not raw:
        return []
    try:
        data: Any = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or data.get("v") != LINKS_JSON_VERSION:
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    result: list[dict[str, str]] = []
    for item in items[:MAX_LINK_ITEMS]:
        if not isinstance(item, dict):
            continue
        href = sanitize_http_url(item.get("href"))
        if href is None:
            continue
        label = str(item.get("label") or "")[:MAX_LINK_LABEL_LEN]
        result.append({"href": href, "label": label})
    return result


def validate_links_json_for_index(raw: str) -> str:
    """Strict validation used during indexing; raises on malformed structure."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LinkSanitizeError("links_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise LinkSanitizeError("links_json must be an object")
    if data.get("v") != LINKS_JSON_VERSION:
        raise LinkSanitizeError("unsupported links_json version")
    items = data.get("items")
    if not isinstance(items, list):
        raise LinkSanitizeError("links_json.items must be a list")
    if len(items) > MAX_LINK_ITEMS:
        raise LinkSanitizeError("too many links")
    cleaned: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise LinkSanitizeError("link item must be an object")
        href = sanitize_http_url(item.get("href"))
        if href is None:
            raise LinkSanitizeError("link href rejected")
        label = str(item.get("label") or "")
        if len(label) > MAX_LINK_LABEL_LEN:
            raise LinkSanitizeError("link label too long")
        cleaned.append({"href": href, "label": label})
    return json.dumps({"v": LINKS_JSON_VERSION, "items": cleaned}, separators=(",", ":"))
