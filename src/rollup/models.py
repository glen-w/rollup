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
GroupType = Literal[
    "standalone",
    "notification_stream",
    "daily_editions",
]
GroupRenderMode = Literal["compact", "expandable"]
FinalReviewIssueType = Literal[
    "style_drift",
    "duplication",
    "date_inconsistency",
    "heading_inconsistency",
    "link_issue",
    "metadata_mismatch",
    "possible_contradiction",
    "length_mismatch",
    "other",
]
FinalReviewSeverity = Literal["minor", "major", "critical"]
FinalReviewStatus = Literal["pass", "pass_with_warnings", "fail"]
FinalReviewSource = Literal["cache", "ollama", "error", "skipped"]
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
class DigestGroup:
    """Grouped related messages for compact digest reading."""

    group_id: str
    group_type: GroupType
    display_name: str
    sender_normalized: str
    folder_name: str
    entries: tuple[DigestEntry, ...]
    group_summary: str | None = None
    group_summary_source: SummarySource = "none"
    render_mode: GroupRenderMode = "compact"


# DigestItem is either a standalone entry or a group of related entries.
DigestItem = DigestEntry | DigestGroup


@dataclass(frozen=True)
class GroupingMetadata:
    groups_created: int
    messages_in_groups: int
    standalone_cards: int
    grouping_counts: dict[str, int]


@dataclass(frozen=True)
class DigestSummaryRouteStat:
    newsletter_type: str
    profile_name: str
    model: str
    count: int


@dataclass(frozen=True)
class DigestSummaryAnomalyRow:
    subject: str
    profile_name: str
    status: str
    stop_reason: str | None
    output_chars: int
    elapsed_seconds: float | None
    cached: bool


@dataclass(frozen=True)
class DigestSummaryMetadata:
    mode: str
    profiles_used: tuple[str, ...]
    models_used: tuple[str, ...]
    summaries_ollama: int
    summaries_cache: int
    summaries_fallback: int
    summaries_errors: int
    selected_profiles: tuple[str, ...] = ()
    output_variants: tuple[str, ...] = ()
    routing_counts: tuple[DigestSummaryRouteStat, ...] = ()
    anomaly_rows: tuple[DigestSummaryAnomalyRow, ...] = ()
    variant_name: str | None = None


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
    summaries_errors: int = 0


@dataclass(frozen=True)
class DigestReport:
    generated_at: datetime
    lookback_days: int
    window_start: datetime
    window_end: datetime
    dated_by_folder: dict[str, tuple[DigestItem, ...]]
    undated: tuple[DigestItem, ...]
    stats: DigestStats
    summary_metadata: DigestSummaryMetadata | None = None
    final_review: FinalReviewResult | None = None
    grouping_metadata: GroupingMetadata | None = None


@dataclass(frozen=True)
class FinalReviewIssue:
    severity: FinalReviewSeverity
    type: FinalReviewIssueType
    location: str
    entry_id: str | None
    description: str
    suggested_fix: str | None
    safe_auto_fix: bool


@dataclass(frozen=True)
class FinalReviewPatch:
    entry_id: str
    field: Literal["summary"]
    replacement: str
    rationale: str


@dataclass(frozen=True)
class FinalReviewResult:
    overall_status: FinalReviewStatus
    safe_to_publish: bool
    issues: tuple[FinalReviewIssue, ...]
    patches: tuple[FinalReviewPatch, ...]
    review_source: FinalReviewSource
    profile_name: str
    model: str
    prompt_version: str
    generated_at: datetime
    digest_fingerprint: str
    review_input_hash: str


@dataclass(frozen=True)
class DigestReviewEntry:
    entry_id: str
    section: str
    subject: str
    sender: str
    date: str | None
    newsletter_type: str
    read_time_minutes: int
    summary_source: str
    summary: str | None
    link_labels: tuple[str, ...]


@dataclass(frozen=True)
class DigestReviewCorpus:
    window_start: str
    window_end: str
    lookback_days: int
    entry_count: int
    summary_metadata: dict | None
    entries: tuple[DigestReviewEntry, ...]
