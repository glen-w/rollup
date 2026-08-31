"""LinkedIn post models (pre-ParsedMessage)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LinkedInPost:
    """One content-search result normalized before pipeline mapping."""

    activity_id: str | None
    author_name: str
    author_member_id: str | None
    text: str
    permalink: str
    created_at: datetime | None
