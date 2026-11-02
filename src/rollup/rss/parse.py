"""Map a feed document to items, and items to ParsedMessage.

Stdlib ElementTree only. Documents that declare a DTD or entities are rejected
before parse. Python 3.10+ does not resolve external entities.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree as ET

from rollup.models import LinkItem, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.rss.config import RssFeed, RssLayout, folder_name_for_feed
from rollup.rss.models import RssItem

MAX_ITEMS_PER_FEED = 30
MAX_BODY_CHARS = 32_000
_TAG_RE = re.compile(r"<[^>]+>")
_SUBJECT_MAX = 280


class RssParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def reject_dangerous_xml(text: str) -> None:
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered or "<!element" in lowered:
        raise RssParseError("rss_doctype_rejected", "Feed XML declares a DTD or entity")


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if _local(child.tag) == name:
            return child
    return None


def _children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(parent) if _local(child.tag) == name]


def parse_item_date(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt is None:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _plain(raw: str) -> str:
    text = unescape(_TAG_RE.sub(" ", raw))
    return " ".join(text.split())


def _link_from(item: ET.Element) -> str | None:
    link_el = _child(item, "link")
    if link_el is not None:
        href = (link_el.get("href") or "").strip()
        if href:
            return href
        if link_el.text and link_el.text.strip():
            return link_el.text.strip()
    for child in _children(item, "link"):
        rel = (child.get("rel") or "alternate").lower()
        href = (child.get("href") or "").strip()
        if href and rel in {"alternate", ""}:
            return href
    return None


def _author_from(item: ET.Element) -> str | None:
    author = _child(item, "author")
    if author is not None:
        name = _child(author, "name")
        if name is not None and _text(name):
            return _text(name)
        if _text(author):
            return _text(author)
    creator = _child(item, "creator")
    if creator is not None and _text(creator):
        return _text(creator)
    return None


def item_key_for(feed_url: str, guid: str | None, link: str | None, title: str) -> str:
    basis = (guid or link or title or "untitled").strip()
    blob = f"{feed_url}\n{basis}".encode()
    return "rss:item:" + hashlib.sha256(blob).hexdigest()


def parse_feed_xml(text: str, *, feed_url: str) -> list[RssItem]:
    reject_dangerous_xml(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RssParseError("rss_xml_invalid", f"Feed XML is not well-formed: {exc}") from exc
    local = _local(root.tag)
    if local == "rss":
        channel = _child(root, "channel")
        nodes = _children(channel, "item") if channel is not None else []
    elif local == "feed":
        nodes = _children(root, "entry")
    else:
        channel = _child(root, "channel")
        nodes = _children(channel, "item") if channel is not None else _children(root, "entry")
    items: list[RssItem] = []
    for node in nodes[:MAX_ITEMS_PER_FEED]:
        title = _plain(_text(_child(node, "title"))) or "(no title)"
        if len(title) > _SUBJECT_MAX:
            title = title[: _SUBJECT_MAX - 1] + "…"
        link = _link_from(node)
        guid_el = _child(node, "guid") or _child(node, "id")
        guid = _text(guid_el) or None
        published = parse_item_date(
            _text(_child(node, "pubDate"))
            or _text(_child(node, "published"))
            or _text(_child(node, "updated"))
            or _text(_child(node, "date"))
        )
        body_raw = (
            _text(_child(node, "content"))
            or _text(_child(node, "description"))
            or _text(_child(node, "summary"))
            or title
        )
        body = _plain(body_raw)[:MAX_BODY_CHARS]
        key = item_key_for(feed_url, guid, link, title)
        items.append(
            RssItem(
                item_key=key,
                feed_url=feed_url,
                title=title,
                link=link,
                author=_author_from(node),
                published_at=published,
                body_text=body,
                raw_bytes=len(body.encode("utf-8")),
            )
        )
    return items


def rss_source_key(feed_url: str) -> str:
    host = (urlparse_host(feed_url) or "feed")[:200]
    return f"rss:feed:{host}"


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


def rss_item_to_parsed_message(
    item: RssItem,
    *,
    feed: RssFeed,
    layout: RssLayout,
    max_body_chars: int,
) -> ParsedMessage:
    folder = folder_name_for_feed(feed, layout=layout)
    body = item.body_text
    warnings: list[str] = []
    if len(body) > max_body_chars:
        body = body[:max_body_chars]
        warnings.append("body_truncated")
    if not body:
        warnings.append("empty_body")
    sender = item.author or feed.display_name or urlparse_host(feed.url) or feed.slug
    link_items: tuple[LinkItem, ...] = ()
    links: tuple[str, ...] = ()
    if item.link:
        link_items = (LinkItem(href=item.link, text="View article", context=None, source_index=0),)
        links = (item.link,)
    preview = body if len(body) <= 2000 else body[:1999] + "…"
    words = len(body.split())
    return ParsedMessage(
        message_key=item.item_key,
        content_hash=compute_content_hash(body),
        folder_name=folder,
        relative_folder_path=folder,
        subject=item.title,
        sender=sender,
        date_raw=item.published_at.isoformat() if item.published_at else "",
        date_parsed=item.published_at,
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=len(links),
        html_section_break_count=0,
        links=links,
        link_items=link_items,
        read_time_minutes=max(1, (words + 199) // 200) if words else 1,
        preview=preview,
        parse_warnings=tuple(warnings),
        source_key=rss_source_key(feed.url),
        list_id=None,
    )
