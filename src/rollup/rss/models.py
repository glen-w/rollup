"""Parsed RSS / Atom items persisted in the listing cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RssItem:
    item_key: str
    feed_url: str
    title: str
    link: str | None
    author: str | None
    published_at: datetime | None
    body_text: str
    raw_bytes: int
