"""Reddit post and catalog models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RedditPost:
    post_id: str
    subreddit: str
    title: str
    selftext: str
    author: str
    permalink: str
    url: str
    score: int
    num_comments: int
    created_at: datetime | None
    over_18: bool = False
    is_self: bool = True


@dataclass(frozen=True)
class RedditCatalogEntry:
    name: str
    title: str | None
    over_18: bool
    fetched_at: datetime
