"""Data models for the Rollup digest pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

NewsletterType = Literal[
    "short_update",
    "multi_section_digest",
    "essay",
    "link_roundup",
    "unclassified",
]
SummarySource = Literal["ollama", "cache", "preview_fallback", "none"]
LinkCategory = Literal[
    "primary_content",
    "content",
    "registration",
    "event",
    "video_audio",
    "document_pdf",
    "calendar",
    "author_profile",
    "share_comment_like",
    "unsubscribe_preferences",
    "tracking_pixel",
    "junk",
    "unknown",
]


@dataclass(frozen=True)
class MboxFolder:
    folder_name: str
    relative_path: str
    mbox_path: Path
    size_bytes: int


@dataclass(frozen=True)
class InventoryEntry:
    folder: MboxFolder
    message_count: int | None
    parse_error: str | None


@dataclass(frozen=True)
class LinkItem:
    href: str
    text: str | None
    context: str | None
    source_index: int


@dataclass(frozen=True)
class ClassifiedLink:
    href: str
    text: str | None
    context: str | None
    source_index: int
    label: str
    domain: str | None
    category: LinkCategory
    priority: int
    is_main: bool
    hidden_reason: str | None
    dedupe_key: str


@dataclass(frozen=True)
class LinkRenderBundle:
    main_links: tuple[ClassifiedLink, ...]
    other_links: tuple[ClassifiedLink, ...]
    hidden_links: tuple[ClassifiedLink, ...]


@dataclass(frozen=True)
class ParsedMessage:
    """Parsed newsletter message.

    message_key: stable identity for dedup and seen_messages.
      Prefer "mid:" + normalized Message-ID; else "fb:" + sha256 composite.
    content_hash: sha256 of normalized body_text; used when duplicate keys appear.
    """

    message_key: str
    content_hash: str
    folder_name: str
    relative_folder_path: str
    subject: str
    sender: str
    date_raw: str
    date_parsed: datetime | None
    body_text: str
    body_html: str | None
    html_heading_count: int
    html_link_count: int
    html_section_break_count: int
    links: tuple[str, ...]
    link_items: tuple[LinkItem, ...]
    read_time_minutes: int
    preview: str
    parse_warnings: tuple[str, ...]


@dataclass(frozen=True)
class ClassifiedMessage:
    parsed: ParsedMessage
    newsletter_type: NewsletterType
    classification_scores: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class DigestEntry:
    classified: ClassifiedMessage
    summary: str | None
    summary_source: SummarySource


@dataclass(frozen=True)
class DigestStats:
    folders_scanned: int
    messages_parsed: int
    dated_included: int
    undated_needing_review: int
    skipped_outside_window: int
    skipped_seen_undated: int
    deduped_messages: int
    parse_errors: int
    summaries_ollama: int
    summaries_cache: int
    summaries_fallback: int


@dataclass(frozen=True)
class DigestReport:
    generated_at: datetime
    lookback_days: int
    window_start: datetime
    window_end: datetime
    dated_by_folder: dict[str, tuple[DigestEntry, ...]]
    undated: tuple[DigestEntry, ...]
    stats: DigestStats
