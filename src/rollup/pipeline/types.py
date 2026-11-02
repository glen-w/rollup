"""Typed stage results and run aggregation for the digest pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rollup.models import (
    DigestEntry,
    DigestGroup,
    DigestItem,
    DigestReport,
    DigestStats,
    DigestSummaryMetadata,
    FinalReviewResult,
    MboxFolder,
    ParsedMessage,
)
from rollup.run_context import RunContext, RunStatus

if TYPE_CHECKING:
    from rollup.gmail.config import GmailTarget
    from rollup.linkedin.config import LinkedInSearch
    from rollup.reddit.config import RedditSub
    from rollup.webpage.models import WebpageQueueItem


@dataclass(frozen=True)
class StageWarning:
    code: str
    message: str
    folder: str | None = None
    count: int = 1


@dataclass(frozen=True)
class StageError:
    code: str
    message: str
    folder: str | None = None


@dataclass(frozen=True)
class ParseCounts:
    messages_seen: int = 0
    messages_parsed: int = 0
    parse_fatal_errors: int = 0
    parse_anomalies: int = 0
    folders_failed: int = 0


@dataclass(frozen=True)
class FilterCounts:
    skipped_outside_window: int = 0
    skipped_seen_undated: int = 0
    deduped_messages: int = 0
    dated_included: int = 0
    undated_included: int = 0
    skipped_disabled_source: int = 0
    always_surface_included: int = 0
    type_overrides_applied: int = 0
    classifier_disagreements: int = 0
    grouping_overrides_applied: int = 0


@dataclass(frozen=True)
class DiscoveryResult:
    folders: tuple[MboxFolder, ...]
    linkedin_searches: tuple[LinkedInSearch, ...] = ()
    reddit_subs: tuple[RedditSub, ...] = ()
    webpage_items: tuple[WebpageQueueItem, ...] = ()
    gmail_targets: tuple[GmailTarget, ...] = ()
    rss_feeds: tuple[Any, ...] = ()
    warnings: tuple[StageWarning, ...] = ()


@dataclass(frozen=True)
class ParseResult:
    messages: tuple[ParsedMessage, ...]
    counts: ParseCounts
    warnings: tuple[StageWarning, ...] = ()
    errors: tuple[StageError, ...] = ()
    mutated_folders: tuple[str, ...] = ()
    mutation_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FilterResult:
    dated_entries: tuple[DigestEntry, ...]
    undated_entries: tuple[DigestEntry, ...]
    counts: FilterCounts
    warnings: tuple[StageWarning, ...] = ()


@dataclass(frozen=True)
class GroupingResult:
    dated_items: tuple[DigestItem, ...]
    undated_items: tuple[DigestItem, ...]
    groups: tuple[DigestGroup, ...] = ()
    reason_codes: tuple[Any, ...] = ()
    warnings: tuple[StageWarning, ...] = ()


@dataclass(frozen=True)
class SummarizeResult:
    dated_entries: tuple[DigestEntry, ...]
    undated_entries: tuple[DigestEntry, ...]
    summary_metadata: DigestSummaryMetadata | None
    rendered_variants: dict[str, tuple[list[DigestEntry], list[DigestEntry]]]
    execution: Any | None = None
    warnings: tuple[StageWarning, ...] = ()
    errors: tuple[StageError, ...] = ()


@dataclass(frozen=True)
class ReviewResult:
    report: DigestReport
    final_review: FinalReviewResult | None = None
    warnings: tuple[StageWarning, ...] = ()
    errors: tuple[StageError, ...] = ()


@dataclass(frozen=True)
class RenderResult:
    markdown: str
    html: str
    output_stem: str
    variant_name: str | None = None
    md_path: Path | None = None
    html_path: Path | None = None


@dataclass(frozen=True)
class DegradationPolicy:
    """Fixed thresholds for when recoverable issues become material."""

    parse_fatal_rate: float = 0.05
    parse_fatal_absolute: int = 10
    summary_error_rate: float = 0.20
    folder_open_failure_is_partial: bool = True


DEFAULT_DEGRADATION_POLICY = DegradationPolicy()


@dataclass
class AggregatedResults:
    discovery: DiscoveryResult | None = None
    parse: ParseResult | None = None
    filter: FilterResult | None = None
    grouping: GroupingResult | None = None
    summarize: SummarizeResult | None = None
    review: ReviewResult | None = None
    renders: list[RenderResult] = field(default_factory=list)
    dated_outputs_written: bool = False
    latest_outputs_updated: bool = False
    usable_digest: bool = False
    source_snapshot: Any | None = None
    hard_failure: bool = False
    hard_failure_reason: str | None = None
    final_review_failed: bool = False
    ollama_enabled: bool = False
    publication_failed: bool = False
    seen_state_failed: bool = False
    seen_state_updated: bool = False
    manifest_write_failed: bool = False
    group_summaries_degraded: bool = False
    apply_patches_applied: int = 0
    apply_patches_attempted: int = 0
    apply_global_skip_reason: str | None = None
    apply_reject_counts: dict[str, int] = field(default_factory=dict)
    contains_auto_edited_prose: bool = False
    group_summary_ollama_calls: int = 0
    group_summary_cache_hits: int = 0
    group_summary_stream_failures: int = 0
    group_summary_cache_write_errors: int = 0
    group_summary_error_counts: dict[str, int] = field(default_factory=dict)
    apply_policy_unattended: bool | None = None
    apply_policy_max_patches: int | None = None
    apply_policy_max_changed_chars: int | None = None
    web_index_failed: bool = False
    web_index_error: str | None = None
    required_writer_failed: bool = False
    optional_writer_failed: bool = False
    archive_failed: bool = False
    writer_artifacts: list[Path] = field(default_factory=list)
    mbox_mutation_detected: bool = False
    linkedin_degraded: bool = False
    reddit_degraded: bool = False
    webpage_degraded: bool = False
    gmail_degraded: bool = False
    rss_degraded: bool = False
    continuity_ledger_failed: bool = False
    webpage_queue_ingest: list[tuple[int, str]] = field(default_factory=list)
    no_input_reason: str | None = None
    messages_included: int = 0

    @property
    def llm_enabled(self) -> bool:
        return self.ollama_enabled

    # Compatibility alias used during rename migration in tests/helpers.
    @property
    def outputs_published(self) -> bool:
        return self.dated_outputs_written

    @outputs_published.setter
    def outputs_published(self, value: bool) -> None:
        self.dated_outputs_written = value


@dataclass(frozen=True)
class DigestRunResult:
    status: RunStatus
    exit_code: int
    context: RunContext
    report: DigestReport | None
    stats: DigestStats | None
    aggregated: AggregatedResults
    md_path: Path | None = None
    html_path: Path | None = None
    manifest_path: Path | None = None
    secondary_manifest_error: str | None = None
    error_message: str | None = None
