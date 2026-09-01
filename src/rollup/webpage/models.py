"""Webpage queue data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

WebpageQueueStatus = Literal["pending", "failed", "ingested"]


@dataclass(frozen=True)
class WebpageQueueItem:
    id: int
    url: str
    url_hash: str
    display_title: str | None
    status: WebpageQueueStatus
    error_code: str | None
    error_message: str | None
    created_at: datetime
    ingested_at: datetime | None
    ingested_message_key: str | None
    ingested_run_id: str | None
    fetched_title: str | None = None
    body_text: str | None = None
    content_hash: str | None = None
    fetched_at: datetime | None = None

    @property
    def has_cached_body(self) -> bool:
        return bool(self.body_text and self.body_text.strip())
