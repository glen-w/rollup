"""Digest pipeline orchestration with typed stage results."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from rollup.linkedin.config import LinkedInSearch
    from rollup.linkedin.fetch import LinkedInClient
    from rollup.reddit.config import RedditSub
    from rollup.webpage.models import WebpageQueueItem

from rollup.clock import Clock, DEFAULT_CLOCK
from rollup.config import Config, compute_date_window
from rollup.discovery import filter_folders, iter_mbox_files
from rollup.effective_run import resolve_effective_run
from rollup.filter import (
    apply_undated_seen_filter,
    build_digest_entries,
    count_summary_sources,
    group_dated_by_folder,
)
from rollup.models import (
    DigestEntry,
    DigestReport,
    DigestStats,
    DigestSummaryMetadata,
    FinalReviewResult,
    MboxFolder,
    ParsedMessage,
)
from rollup.render import (
    atomic_write_digest,
    digest_output_stem,
    render_html,
    render_markdown,
)
from rollup.run_context import RunContext, RunStatus
from rollup.run_options import GroupingConfig, ManifestConfig, RunOptions
from rollup.summary_plan import SummaryCliOptions, resolve_summary_plan
from rollup.effort import resolve_profile_set
from rollup.summary_profiles import (
    get_canonical_newsletter_types,
    require_valid_summary_profile_set,
)

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PARTIAL = 2


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
    reddit_subs: tuple["RedditSub", ...] = ()
    webpage_items: tuple[WebpageQueueItem, ...] = ()
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
    dated_items: tuple[Any, ...]  # DigestEntry | DigestGroup
    undated_items: tuple[Any, ...]
    groups: tuple[Any, ...] = ()
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

    @property
    def llm_enabled(self) -> bool:
        return self.ollama_enabled
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
    writer_artifacts: list[Path] = field(default_factory=list)
    mbox_mutation_detected: bool = False
    linkedin_degraded: bool = False
    reddit_degraded: bool = False
    webpage_degraded: bool = False
    webpage_queue_ingest: list[tuple[int, str]] = field(default_factory=list)
    no_input_reason: str | None = None
    messages_included: int = 0

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


def derive_run_status(
    aggregated: AggregatedResults,
    *,
    dry_run: bool = False,
    policy: DegradationPolicy = DEFAULT_DEGRADATION_POLICY,
) -> RunStatus:
    """Sole authority for run status. Maps to exit codes in status_to_exit_code."""
    if dry_run and not aggregated.hard_failure:
        return "dry_run"
    if aggregated.hard_failure or not aggregated.usable_digest:
        return "failure"

    if policy.folder_open_failure_is_partial and aggregated.parse:
        if aggregated.parse.counts.folders_failed > 0:
            return "partial"

    if aggregated.parse:
        seen = aggregated.parse.counts.messages_seen
        fatals = aggregated.parse.counts.parse_fatal_errors
        if seen > 0:
            rate = fatals / seen
            threshold = min(
                policy.parse_fatal_rate * seen,
                float(policy.parse_fatal_absolute),
            )
            if fatals > threshold:
                return "partial"
        elif fatals > policy.parse_fatal_absolute:
            return "partial"

    if aggregated.llm_enabled and aggregated.summarize:
        meta = aggregated.summarize.summary_metadata
        if meta is not None:
            total = (
                meta.summaries_ollama
                + meta.summaries_litellm
                + meta.summaries_cache
                + meta.summaries_fallback
                + meta.summaries_errors
            )
            if total > 0 and (meta.summaries_errors / total) > policy.summary_error_rate:
                return "partial"

    if aggregated.final_review_failed:
        return "partial"

    if aggregated.publication_failed:
        return "partial"

    if aggregated.group_summaries_degraded:
        return "partial"

    if aggregated.seen_state_failed:
        return "partial"

    if aggregated.manifest_write_failed:
        return "partial"

    if aggregated.optional_writer_failed:
        return "partial"

    if aggregated.mbox_mutation_detected:
        return "partial"

    if aggregated.linkedin_degraded:
        return "partial"

    if aggregated.reddit_degraded:
        return "partial"

    if aggregated.webpage_degraded:
        return "partial"

    if aggregated.required_writer_failed:
        return "failure"

    # web_index_failed is secondary: does not alone force partial
    return "success"


def status_to_exit_code(status: RunStatus) -> int:
    if status in ("success", "dry_run"):
        return EXIT_SUCCESS
    if status == "partial":
        return EXIT_PARTIAL
    return EXIT_FAILURE


def evaluate_no_input(
    *,
    folders_include: tuple[str, ...],
    discovery: DiscoveryResult,
    parse: ParseResult,
) -> str | None:
    """Return a hard-failure reason when the run has no usable input, else None."""
    has_mbox = bool(discovery.folders)
    has_linkedin = bool(discovery.linkedin_searches)
    has_reddit = bool(discovery.reddit_subs)
    has_webpage = bool(discovery.webpage_items)

    if folders_include and not has_mbox and not has_linkedin and not has_reddit and not has_webpage:
        return (
            "No folders matched explicit include "
            f"({', '.join(folders_include)}); refusing to publish"
        )
    if not has_mbox and not has_linkedin and not has_reddit and not has_webpage:
        return (
            "No readable mbox folders, LinkedIn searches, Reddit subs, or webpage "
            "queue items; refusing to publish"
        )

    if has_mbox:
        if (
            parse.counts.messages_parsed == 0
            and parse.counts.messages_seen > 0
            and parse.counts.parse_fatal_errors > 0
        ):
            return "All candidate messages failed parsing; refusing to publish"
        if (
            parse.counts.messages_parsed == 0
            and parse.counts.folders_failed >= len(discovery.folders)
        ):
            return "No folders were readable; refusing to publish"

    if not has_mbox and has_linkedin and parse.counts.messages_parsed == 0:
        linkedin_failed = any(
            w.code.startswith("linkedin_") and w.code != "linkedin_dry_run"
            for w in parse.warnings
        )
        if linkedin_failed:
            return (
                "LinkedIn fetch failed and no other input sources; "
                "refusing to publish"
            )

    if not has_mbox and not has_linkedin and has_webpage and parse.counts.messages_parsed == 0:
        webpage_failed = any(
            w.code.startswith("webpage_") and w.code != "webpage_dry_run"
            for w in parse.warnings
        )
        if webpage_failed:
            return (
                "Webpage fetch failed and no other input sources; "
                "refusing to publish"
            )

    if not has_mbox and not has_linkedin and not has_webpage and has_reddit and parse.counts.messages_parsed == 0:
        reddit_failed = any(
            w.code.startswith("reddit_") and w.code != "reddit_dry_run"
            for w in parse.warnings
        )
        if reddit_failed:
            return (
                "Reddit fetch failed and no other input sources; "
                "refusing to publish"
            )

    return None


def stage_discover(
    config: Config, *, generated_at: datetime | None = None
) -> DiscoveryResult:
    from rollup.linkedin.config import filter_linkedin_searches
    from rollup.reddit.config import filter_reddit_subs
    from rollup.webpage.config import MAX_WEBPAGE_FETCHES, WEBPAGE_FOLDER_NAME
    from rollup.webpage.queue import load_for_digest

    folders = list(iter_mbox_files(config.root))
    folders = filter_folders(folders, config.folders_include, config.folders_exclude)
    linkedin_searches: tuple[LinkedInSearch, ...] = ()
    if config.linkedin_enabled and config.linkedin.searches:
        linkedin_searches = filter_linkedin_searches(
            config.linkedin.searches,
            folders_include=config.folders_include,
            folders_exclude=config.folders_exclude,
            layout=config.linkedin.layout,
        )
    reddit_subs: tuple = ()
    if config.reddit_enabled and config.reddit.subs:
        reddit_subs = filter_reddit_subs(
            config.reddit,
            folders_include=config.folders_include,
            folders_exclude=config.folders_exclude,
        )
    webpage_items: tuple[WebpageQueueItem, ...] = ()
    if config.webpage_enabled:
        exclude = set(config.folders_exclude)
        include = set(config.folders_include)
        webpage_allowed = WEBPAGE_FOLDER_NAME not in exclude and (
            not include or WEBPAGE_FOLDER_NAME in include
        )
        if webpage_allowed:
            db_path = config.db_path
            if db_path.is_file():
                from datetime import timezone

                from rollup.state import init_db

                when = generated_at or datetime.now(timezone.utc)
                window_start, window_end = compute_date_window(
                    when, config.lookback_days
                )
                conn = init_db(db_path)
                try:
                    webpage_items = load_for_digest(
                        conn,
                        window_start=window_start,
                        window_end=window_end,
                        fetch_limit=MAX_WEBPAGE_FETCHES,
                    )
                finally:
                    conn.close()
    return DiscoveryResult(
        folders=tuple(folders),
        linkedin_searches=linkedin_searches,
        reddit_subs=reddit_subs,
        webpage_items=webpage_items,
    )


def stage_parse(config: Config, folders: tuple[MboxFolder, ...]) -> ParseResult:
    from rollup.mbox_identity import classify_mbox_mutation, snapshot_mbox
    from rollup.parse import parse_mbox_folder

    messages: list[ParsedMessage] = []
    warnings: list[StageWarning] = []
    errors: list[StageError] = []
    seen = 0
    fatal = 0
    anomalies = 0
    folders_failed = 0
    mutated_folders: list[str] = []
    mutation_codes: list[str] = []

    for folder in folders:
        logger.info("Parsing %s (%s)", folder.folder_name, folder.mbox_path)
        before = snapshot_mbox(folder.mbox_path)
        msgs, err_count, folder_errors = parse_mbox_folder(
            folder, config.max_body_chars, config.max_display_links
        )
        after = snapshot_mbox(folder.mbox_path)
        mutation = classify_mbox_mutation(before, after)
        if mutation:
            mutated_folders.append(folder.folder_name)
            mutation_codes.append(mutation)
            warnings.append(
                StageWarning(
                    code=mutation,
                    message=f"mbox changed during parse ({mutation})",
                    folder=folder.folder_name,
                )
            )
            logger.warning(
                "Mbox mutation %s on %s; excluding folder results from publication",
                mutation,
                folder.folder_name,
            )
            # Exclude changed-folder results from the published digest.
            continue
        # Approximate seen: parsed + errors for this folder.
        folder_seen = len(msgs) + err_count
        if folder_errors:
            folders_failed += 1
            fatal += 1
            errors.append(
                StageError(
                    code="mbox_open",
                    message=folder_errors[0],
                    folder=folder.folder_name,
                )
            )
            logger.error("Folder %s: %s", folder.folder_name, folder_errors[0])
            continue
        seen += folder_seen
        fatal += err_count
        for msg in msgs:
            anomalies += len(msg.parse_warnings)
            messages.append(msg)

    counts = ParseCounts(
        messages_seen=seen,
        messages_parsed=len(messages),
        parse_fatal_errors=fatal,
        parse_anomalies=anomalies,
        folders_failed=folders_failed,
    )
    return ParseResult(
        messages=tuple(messages),
        counts=counts,
        warnings=tuple(warnings),
        errors=tuple(errors),
        mutated_folders=tuple(mutated_folders),
        mutation_codes=tuple(mutation_codes),
    )


def stage_parse_linkedin(
    config: Config,
    searches: tuple["LinkedInSearch", ...],
    *,
    dry_run: bool,
    client: "LinkedInClient | None" = None,
) -> tuple[list[ParsedMessage], list[StageWarning], bool]:
    """Fetch LinkedIn searches and map to ParsedMessage. Returns (msgs, warnings, degraded)."""
    if not searches:
        return [], [], False

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.linkedin.article import enrich_posts_with_articles
    from rollup.linkedin.fetch import LinkedInFetchError, fetch_search_posts
    from rollup.linkedin.parse import linkedin_post_to_parsed_message
    from rollup.linkedin.session import (
        LinkedInSessionError,
        build_linkedin_session,
        linkedin_cookie_configured,
    )

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    degraded = False
    article_session = None
    if config.linkedin.article_fetch:
        try:
            article_session = build_linkedin_session()
        except LinkedInSessionError:
            article_session = None

    if dry_run:
        from rollup.linkedin.session import jsession_id_configured
        from rollup.linkedin.url import from_member_ids

        if not linkedin_cookie_configured():
            warnings.append(
                StageWarning(
                    code="linkedin_no_cookie",
                    message=(
                        "LinkedIn enabled but ROLLUP_LINKEDIN_LI_AT is not set "
                        "(dry-run; no fetch)"
                    ),
                )
            )
        needs_voyager = any(from_member_ids(s.url) for s in searches)
        if needs_voyager and not jsession_id_configured():
            warnings.append(
                StageWarning(
                    code="linkedin_no_jsession",
                    message=(
                        "fromMember search requires ROLLUP_LINKEDIN_JSESSIONID "
                        "(dry-run; no fetch)"
                    ),
                )
            )
        for search in searches:
            warnings.append(
                StageWarning(
                    code="linkedin_dry_run",
                    message=f"Would fetch LinkedIn search {search.slug}",
                    folder=search.folder_name,
                )
            )
        degraded = (not linkedin_cookie_configured()) or (
            needs_voyager and not jsession_id_configured()
        )
        return messages, warnings, degraded

    for search in searches:
        try:
            posts = fetch_search_posts(
                search,
                lookback_days=config.lookback_days,
                client=client,
            )
            if article_session is not None and config.linkedin.article_fetch:
                posts, article_warnings = enrich_posts_with_articles(
                    posts,
                    article_session,
                    enabled=True,
                )
            else:
                article_warnings = [() for _ in posts]
            for post, post_warnings in zip(posts, article_warnings, strict=True):
                messages.append(
                    linkedin_post_to_parsed_message(
                        post,
                        search_slug=search.slug,
                        max_body_chars=config.max_body_chars,
                        layout=config.linkedin.layout,
                        extra_warnings=post_warnings,
                    )
                )
        except LinkedInFetchError as exc:
            degraded = True
            warnings.append(
                StageWarning(
                    code="linkedin_fetch_failed",
                    message=sanitize_provider_message(str(exc)),
                    folder=search.folder_name,
                )
            )
            logger.warning(
                "LinkedIn fetch failed for %s: %s",
                search.slug,
                sanitize_provider_message(str(exc)),
            )

    return messages, warnings, degraded


def stage_parse_reddit(
    config: Config,
    subs: tuple,
    *,
    dry_run: bool,
    generated_at: datetime | None = None,
    client=None,
) -> tuple[list[ParsedMessage], list[StageWarning], bool]:
    """Fetch Reddit subs and map to ParsedMessage. Returns (msgs, warnings, degraded)."""
    if not subs:
        return [], [], False

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.reddit.fetch import RedditFetchError, fetch_posts_for_subs
    from rollup.reddit.parse import reddit_post_to_parsed_message
    from rollup.reddit.session import RedditSessionError

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    degraded = False

    if dry_run:
        for sub in subs:
            warnings.append(
                StageWarning(
                    code="reddit_dry_run",
                    message=f"Would fetch r/{sub.name} via public RSS",
                    folder=config.reddit.layout,
                )
            )
        return messages, warnings, degraded

    when = generated_at or datetime.now().astimezone()
    window_start, window_end = compute_date_window(when, config.lookback_days)

    try:
        posts_by_sub = fetch_posts_for_subs(
            subs,
            config=config.reddit,
            lookback_days=config.lookback_days,
            client=client,
            window_start=window_start,
            window_end=window_end,
        )
    except (RedditFetchError, RedditSessionError) as exc:
        degraded = True
        warnings.append(
            StageWarning(
                code="reddit_fetch_failed",
                message=sanitize_provider_message(str(exc)),
            )
        )
        logger.warning("Reddit fetch failed: %s", sanitize_provider_message(str(exc)))
        return messages, warnings, degraded

    for sub_name, posts in posts_by_sub.items():
        for post in posts:
            messages.append(
                reddit_post_to_parsed_message(
                    post,
                    layout=config.reddit.layout,
                    max_body_chars=config.max_body_chars,
                )
            )
    return messages, warnings, degraded


def merge_reddit_parse(
    prior: ParseResult,
    reddit_messages: list[ParsedMessage],
    reddit_warnings: list[StageWarning],
) -> ParseResult:
    """Combine prior parse results with Reddit messages."""
    if not reddit_messages and not reddit_warnings:
        return prior
    combined = list(prior.messages) + reddit_messages
    reddit_seen = len(reddit_messages)
    return ParseResult(
        messages=tuple(combined),
        counts=ParseCounts(
            messages_seen=prior.counts.messages_seen + reddit_seen,
            messages_parsed=len(combined),
            parse_fatal_errors=prior.counts.parse_fatal_errors,
            parse_anomalies=prior.counts.parse_anomalies,
            folders_failed=prior.counts.folders_failed,
        ),
        warnings=prior.warnings + tuple(reddit_warnings),
        errors=prior.errors,
        mutated_folders=prior.mutated_folders,
        mutation_codes=prior.mutation_codes,
    )


def merge_linkedin_parse(
    mbox_parse: ParseResult,
    linkedin_messages: list[ParsedMessage],
    linkedin_warnings: list[StageWarning],
) -> ParseResult:
    """Combine mbox and LinkedIn parse results."""
    if not linkedin_messages and not linkedin_warnings:
        return mbox_parse
    combined = list(mbox_parse.messages) + linkedin_messages
    li_seen = len(linkedin_messages)
    return ParseResult(
        messages=tuple(combined),
        counts=ParseCounts(
            messages_seen=mbox_parse.counts.messages_seen + li_seen,
            messages_parsed=len(combined),
            parse_fatal_errors=mbox_parse.counts.parse_fatal_errors,
            parse_anomalies=mbox_parse.counts.parse_anomalies,
            folders_failed=mbox_parse.counts.folders_failed,
        ),
        warnings=mbox_parse.warnings + tuple(linkedin_warnings),
        errors=mbox_parse.errors,
        mutated_folders=mbox_parse.mutated_folders,
        mutation_codes=mbox_parse.mutation_codes,
    )


def stage_parse_webpage(
    config: Config,
    items: tuple["WebpageQueueItem", ...],
    *,
    dry_run: bool,
    generated_at: datetime,
) -> tuple[list[ParsedMessage], list[StageWarning], bool, list[tuple[int, str]]]:
    """Map webpage queue items to ParsedMessage, fetching only on cache miss."""
    if not items:
        return [], [], False, []

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.state import init_db
    from rollup.webpage.config import WEBPAGE_FOLDER_NAME
    from rollup.webpage.fetch import WebpageFetchError, fetch_webpage
    from rollup.webpage.parse import webpage_to_parsed_message
    from rollup.webpage.queue import mark_failed, store_fetched

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    ingest_map: list[tuple[int, str]] = []
    degraded = False

    conn = init_db(config.db_path)
    try:
        for item in items:
            if item.has_cached_body:
                msg = webpage_to_parsed_message(
                    url=item.url,
                    title=item.fetched_title or None,
                    body_text=item.body_text or "",
                    saved_at=item.created_at,
                    display_title=item.display_title,
                    max_body_chars=config.max_body_chars,
                )
                messages.append(msg)
                ingest_map.append((item.id, msg.message_key))
                continue

            if dry_run:
                warnings.append(
                    StageWarning(
                        code="webpage_dry_run",
                        message=f"Would fetch webpage queue item {item.url}",
                        folder=WEBPAGE_FOLDER_NAME,
                    )
                )
                continue

            try:
                result = fetch_webpage(item.url)
            except WebpageFetchError as exc:
                degraded = True
                mark_failed(
                    conn,
                    item.id,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                warnings.append(
                    StageWarning(
                        code=exc.code,
                        message=sanitize_provider_message(exc.message),
                        folder=WEBPAGE_FOLDER_NAME,
                    )
                )
                logger.warning(
                    "Webpage fetch failed for %s: %s",
                    item.url[:80],
                    sanitize_provider_message(exc.message),
                )
                continue

            msg = webpage_to_parsed_message(
                url=result.url,
                title=result.title or None,
                body_text=result.body_text,
                saved_at=item.created_at,
                display_title=item.display_title,
                max_body_chars=config.max_body_chars,
                extra_warnings=result.warnings,
            )
            store_fetched(
                conn,
                item.id,
                title=result.title or None,
                body_text=result.body_text,
                content_hash=msg.content_hash,
                message_key=msg.message_key,
                fetched_at=generated_at,
            )
            messages.append(msg)
            ingest_map.append((item.id, msg.message_key))
            for code in result.warnings:
                warnings.append(
                    StageWarning(
                        code=code,
                        message=code,
                        folder=WEBPAGE_FOLDER_NAME,
                    )
                )
    finally:
        conn.close()

    return messages, warnings, degraded, ingest_map


def merge_webpage_parse(
    parse_result: ParseResult,
    webpage_messages: list[ParsedMessage],
    webpage_warnings: list[StageWarning],
) -> ParseResult:
    """Combine mbox/LinkedIn and webpage parse results."""
    if not webpage_messages and not webpage_warnings:
        return parse_result
    combined = list(parse_result.messages) + webpage_messages
    web_seen = len(webpage_messages)
    return ParseResult(
        messages=tuple(combined),
        counts=ParseCounts(
            messages_seen=parse_result.counts.messages_seen + web_seen,
            messages_parsed=len(combined),
            parse_fatal_errors=parse_result.counts.parse_fatal_errors,
            parse_anomalies=parse_result.counts.parse_anomalies,
            folders_failed=parse_result.counts.folders_failed,
        ),
        warnings=parse_result.warnings + tuple(webpage_warnings),
        errors=parse_result.errors,
        mutated_folders=parse_result.mutated_folders,
        mutation_codes=parse_result.mutation_codes,
    )


def stage_filter(
    messages: tuple[ParsedMessage, ...],
    *,
    generated_at: datetime,
    lookback_days: int,
    no_ollama: bool,
    seen_keys: set[str],
    include_seen_undated: bool,
    snapshot=None,
) -> FilterResult:
    from rollup.filter import BuildDigestResult

    built = build_digest_entries(
        list(messages),
        generated_at,
        lookback_days,
        no_ollama,
        snapshot=snapshot,
    )
    if isinstance(built, BuildDigestResult):
        dated_entries = built.dated_entries
        undated_entries = built.undated_entries
        skipped_window = built.skipped_outside_window
        deduped = built.deduped_messages
        skipped_disabled = built.skipped_disabled_source
        type_overrides = built.type_overrides_applied
        disagreements = built.classifier_disagreements
    else:
        dated_entries, undated_entries, skipped_window, deduped = built
        skipped_disabled = type_overrides = disagreements = 0

    undated_to_render, skipped_seen, always_surfaced = apply_undated_seen_filter(
        undated_entries, seen_keys, include_seen_undated, snapshot=snapshot
    )
    counts = FilterCounts(
        skipped_outside_window=skipped_window,
        skipped_seen_undated=skipped_seen,
        deduped_messages=deduped,
        dated_included=len(dated_entries),
        undated_included=len(undated_to_render),
        skipped_disabled_source=skipped_disabled,
        always_surface_included=always_surfaced,
        type_overrides_applied=type_overrides,
        classifier_disagreements=disagreements,
    )
    return FilterResult(
        dated_entries=tuple(dated_entries),
        undated_entries=tuple(undated_to_render),
        counts=counts,
    )


def stage_group(
    dated_entries: tuple[DigestEntry, ...],
    undated_entries: tuple[DigestEntry, ...],
    grouping: GroupingConfig,
    snapshot=None,
    reddit_config=None,
) -> GroupingResult:
    """Apply grouping when enabled; otherwise pass entries through as items."""
    from rollup.reddit.config import RedditConfig

    if not grouping.enabled and reddit_config is None:
        return GroupingResult(
            dated_items=dated_entries,
            undated_items=undated_entries,
        )
    from rollup.grouping import apply_grouping

    applied = apply_grouping(
        dated_entries,
        undated_entries,
        grouping,
        snapshot=snapshot,
        reddit_config=reddit_config if isinstance(reddit_config, RedditConfig) else None,
    )
    return GroupingResult(
        dated_items=applied.dated_items,
        undated_items=applied.undated_items,
        groups=applied.groups,
        reason_codes=applied.reason_codes,
    )


def _flatten_items_to_entries(items: tuple[Any, ...]) -> list[DigestEntry]:
    """Flatten DigestItem list to DigestEntry list for summarisation."""
    from rollup.models import DigestEntry as DE

    out: list[DigestEntry] = []
    for item in items:
        if isinstance(item, DE):
            out.append(item)
        elif hasattr(item, "entries"):
            out.extend(item.entries)
        else:
            out.append(item)
    return out


def _rebuild_items_with_summaries(
    items: tuple[Any, ...],
    summarized: list[DigestEntry],
) -> tuple[Any, ...]:
    """Re-attach summarized entries into the original item structure."""
    from rollup.models import DigestEntry as DE
    from rollup.models import DigestGroup

    by_key = {e.classified.parsed.message_key: e for e in summarized}
    rebuilt: list[Any] = []
    for item in items:
        if isinstance(item, DE):
            key = item.classified.parsed.message_key
            rebuilt.append(by_key.get(key, item))
        elif isinstance(item, DigestGroup):
            new_entries = tuple(
                by_key.get(e.classified.parsed.message_key, e) for e in item.entries
            )
            rebuilt.append(replace(item, entries=new_entries))
        else:
            rebuilt.append(item)
    return tuple(rebuilt)


def stage_summarize(
    config: Config,
    dated_entries: list[DigestEntry],
    undated_entries: list[DigestEntry],
    profile_set,
    conn,
    *,
    allow_network: bool,
    quiet: bool,
    snapshot=None,
) -> SummarizeResult:
    if not allow_network:
        return SummarizeResult(
            dated_entries=tuple(dated_entries),
            undated_entries=tuple(undated_entries),
            summary_metadata=None,
            rendered_variants={},
        )

    from rollup.summarize import execute_summary_plan

    routing = config.summary_type_routing
    if routing is None:
        routing = not config.summary_profile and not config.summary_variants
    cli_options = SummaryCliOptions(
        summary_profile=config.summary_profile,
        summary_variants=config.summary_variants,
        summary_type_routing=routing,
    )
    all_entries = dated_entries + undated_entries
    policy_by_mk: dict[str, object] = {}
    if snapshot is not None:
        for entry in all_entries:
            policy_by_mk[entry.classified.parsed.message_key] = snapshot.policy_for(
                entry.classified.parsed.source_key
            )
    plan_warnings: list[str] = []
    plan = resolve_summary_plan(
        all_entries,
        profile_set,
        cli_options,
        policy_by_message_key=policy_by_mk,
        warnings=plan_warnings,
    )
    for msg in plan_warnings:
        logger.warning("%s", msg)
    from rollup.llm_validate import LlmJobValidationError, validate_executable_llm_jobs

    try:
        validate_executable_llm_jobs(config, plan)
    except LlmJobValidationError as exc:
        raise RuntimeError(str(exc)) from exc
    execution = execute_summary_plan(
        entries=all_entries,
        plan=plan,
        ollama_url=config.ollama_url,
        default_model=config.ollama_model,
        max_chars=config.max_chars_for_llm,
        allow_remote=config.allow_remote_ollama,
        conn=conn,
        rebuild=config.rebuild_summaries,
        quiet=quiet,
        llm_api_base=config.llm_api_base,
    )
    dated_count = len(dated_entries)
    rendered_variants: dict[str, tuple[list[DigestEntry], list[DigestEntry]]] = {}
    for variant_name, rendered in execution.entries_by_variant.items():
        rendered_variants[variant_name] = (
            rendered[:dated_count],
            rendered[dated_count:],
        )
    default_variant_name = (
        "default"
        if "default" in rendered_variants
        else next(iter(rendered_variants))
    )
    dated_out, undated_out = rendered_variants[default_variant_name]
    summary_metadata = execution.summary_metadata_by_variant.get(default_variant_name)
    return SummarizeResult(
        dated_entries=tuple(dated_out),
        undated_entries=tuple(undated_out),
        summary_metadata=summary_metadata,
        rendered_variants=rendered_variants,
        execution=execution,
    )


def build_digest_report(
    *,
    generated_at: datetime,
    lookback_days: int,
    window_start: datetime,
    window_end: datetime,
    dated_entries: list[DigestEntry] | list[Any],
    undated_entries: list[DigestEntry] | list[Any],
    stats: DigestStats,
    summary_metadata: DigestSummaryMetadata | None,
    dated_by_folder: dict | None = None,
) -> DigestReport:
    if dated_by_folder is None:
        # Flatten groups for folder grouping when needed.
        flat = _flatten_items_to_entries(tuple(dated_entries))
        dated_by_folder = group_dated_by_folder(flat)
    flat_undated = _flatten_items_to_entries(tuple(undated_entries))
    return DigestReport(
        generated_at=generated_at,
        lookback_days=lookback_days,
        window_start=window_start,
        window_end=window_end,
        dated_by_folder=dated_by_folder,
        undated=tuple(flat_undated),
        stats=stats,
        summary_metadata=summary_metadata,
    )


@dataclass
class _DigestSession:
    """Mutable run state for ``run_digest`` phase helpers."""

    config: Config
    run_options: RunOptions
    grouping: GroupingConfig
    manifest_config: ManifestConfig
    acquire_lock: bool
    output_writers: list | None
    writer_cli_args: object | None
    ctx: RunContext
    generated_at: datetime
    aggregated: AggregatedResults
    window_start: datetime
    window_end: datetime
    effective_run: Any
    resolved_apply_policy: Any
    lock: Any = None
    conn: Any = None
    report: DigestReport | None = None
    stats: DigestStats | None = None
    md_path: Path | None = None
    html_path: Path | None = None
    manifest_path: Path | None = None
    secondary_manifest_error: str | None = None
    error_message: str | None = None
    status: RunStatus = "failure"
    manifest_builder: Any = None
    summarize_result: SummarizeResult | None = None


def _failure_result(session: _DigestSession) -> DigestRunResult:
    return DigestRunResult(
        status=session.status,
        exit_code=EXIT_FAILURE,
        context=session.ctx,
        report=session.report,
        stats=session.stats,
        aggregated=session.aggregated,
        md_path=session.md_path,
        html_path=session.html_path,
        error_message=session.error_message,
    )


def _validate_run_paths(session: _DigestSession) -> DigestRunResult | None:
    from rollup.safety import SafetyError, validate_writable_run_paths

    config = session.config
    try:
        validate_writable_run_paths(
            newsletter_root=config.root,
            mail_root=config.mail_root,
            output_dir=config.output_dir,
            state_dir=config.state_dir,
            log_dir=config.log_dir,
            db_path=config.db_path,
        )
    except SafetyError as exc:
        session.aggregated.hard_failure = True
        session.aggregated.hard_failure_reason = str(exc)
        session.error_message = str(exc)
        session.status = "failure"
        return _failure_result(session)
    return None


def _prepare_lock_and_manifest(session: _DigestSession) -> DigestRunResult | None:
    from rollup.manifest import ManifestBuilder
    from rollup.run_lock import RunLockError, acquire_run_lock

    config = session.config
    run_options = session.run_options
    if run_options.write_manifest and not run_options.dry_run:
        session.manifest_builder = ManifestBuilder(
            session.ctx,
            config=config,
            run_options=run_options,
            grouping=session.grouping,
            manifest_config=session.manifest_config,
            window_start=session.window_start,
            window_end=session.window_end,
        )

    if session.acquire_lock and not run_options.dry_run:
        try:
            session.lock = acquire_run_lock(
                config.state_dir, session.ctx.run_id, started_at=session.generated_at
            )
            if getattr(session.lock, "stale_recovered", False):
                session.ctx.add_event(
                    "stale_lock_recovered",
                    "Recovered stale run lock",
                    level="warning",
                )
        except RunLockError as exc:
            session.aggregated.hard_failure = True
            session.aggregated.hard_failure_reason = str(exc)
            session.error_message = str(exc)
            session.status = "failure"
            if session.manifest_builder is not None:
                session.manifest_builder.record_failure(exc)
                session.manifest_builder.finalize(
                    status="failure", aggregated=session.aggregated
                )
            return _failure_result(session)
    return None


def _run_core_stages(session: _DigestSession) -> DigestRunResult | None:
    """Discover → parse → filter → group → summarize → report (+ group summaries)."""
    config = session.config
    run_options = session.run_options
    grouping = session.grouping
    aggregated = session.aggregated
    generated_at = session.generated_at
    effective_run = session.effective_run

    profile_set = require_valid_summary_profile_set(
        resolve_profile_set(
            effort=config.effort,
            summary_profile_set_path=config.summary_profile_set_path,
            effort_overrides=config.effort_overrides,
            single_model=config.single_model,
            llm_provider=config.llm_provider,
        ),
        get_canonical_newsletter_types(),
    )

    discovery = stage_discover(config, generated_at=generated_at)
    aggregated.discovery = discovery
    logger.info(
        "Digest: root=%s folders=%d linkedin=%d reddit=%d webpage=%d lookback=%dd dry_run=%s no_ollama=%s",
        config.root,
        len(discovery.folders),
        len(discovery.linkedin_searches),
        len(discovery.reddit_subs),
        len(discovery.webpage_items),
        config.lookback_days,
        run_options.dry_run,
        config.no_ollama,
    )

    parse_result = stage_parse(config, discovery.folders)
    li_messages, li_warnings, linkedin_degraded = stage_parse_linkedin(
        config,
        discovery.linkedin_searches,
        dry_run=run_options.dry_run,
    )
    parse_result = merge_linkedin_parse(parse_result, li_messages, li_warnings)
    aggregated.linkedin_degraded = linkedin_degraded
    rd_messages, rd_warnings, reddit_degraded = stage_parse_reddit(
        config,
        discovery.reddit_subs,
        dry_run=run_options.dry_run,
        generated_at=generated_at,
    )
    parse_result = merge_reddit_parse(parse_result, rd_messages, rd_warnings)
    aggregated.reddit_degraded = reddit_degraded
    wp_messages, wp_warnings, webpage_degraded, wp_ingest = stage_parse_webpage(
        config,
        discovery.webpage_items,
        dry_run=run_options.dry_run,
        generated_at=generated_at,
    )
    parse_result = merge_webpage_parse(parse_result, wp_messages, wp_warnings)
    aggregated.webpage_degraded = webpage_degraded
    aggregated.webpage_queue_ingest = wp_ingest
    aggregated.parse = parse_result
    if parse_result.mutated_folders:
        aggregated.mbox_mutation_detected = True

    no_input = evaluate_no_input(
        folders_include=config.folders_include,
        discovery=discovery,
        parse=parse_result,
    )
    if no_input:
        aggregated.hard_failure = True
        aggregated.hard_failure_reason = no_input
        aggregated.no_input_reason = no_input
        session.error_message = no_input
        session.status = "failure"
        if session.manifest_builder is not None:
            session.manifest_builder.record_failure(RuntimeError(no_input))
            session.manifest_builder.finalize(
                status="failure", aggregated=aggregated
            )
        return _failure_result(session)

    if effective_run.allow_summary_network or effective_run.allow_final_review_network:
        from rollup.llm_client import validate_llm_api_base
        from rollup.llm_validate import LlmJobValidationError, validate_executable_llm_jobs
        from rollup.summarize import OllamaError, validate_ollama_url

        try:
            validate_llm_api_base(config.llm_api_base)
            if effective_run.allow_summary_network:
                validate_ollama_url(config.ollama_url, config.allow_remote_ollama)
            validate_executable_llm_jobs(config, None)
        except (OllamaError, LlmJobValidationError) as exc:
            aggregated.hard_failure = True
            session.error_message = str(exc)
            raise

    seen_keys: set[str] = set()
    snapshot = None
    if not run_options.dry_run:
        from rollup.source_registry import (
            load_SourceRegistrySnapshot,
            observe_sources,
        )
        from rollup.state import ensure_final_review_schema, init_db, load_seen_keys

        # Canonical init always materializes full schema (including caches).
        session.conn = init_db(config.db_path)
        if config.final_review_enabled:
            ensure_final_review_schema(session.conn)
        if config.group_summaries_enabled:
            from rollup.state import ensure_group_summary_schema

            ensure_group_summary_schema(session.conn)
        seen_keys = load_seen_keys(session.conn)
        observe_result = observe_sources(
            session.conn, parse_result.messages, generated_at=generated_at
        )
        needed = {m.source_key for m in parse_result.messages if m.source_key}
        snapshot = load_SourceRegistrySnapshot(
            session.conn,
            needed,
            discovered_this_run=observe_result.discovered_this_run,
            messages_unidentifiable_source=observe_result.messages_unidentifiable,
        )
    else:
        from rollup.source_models import empty_defaults_snapshot

        snapshot = empty_defaults_snapshot(
            messages_unidentifiable_source=sum(
                1 for m in parse_result.messages if not m.source_key
            )
        )

    filter_result = stage_filter(
        parse_result.messages,
        generated_at=generated_at,
        lookback_days=config.lookback_days,
        no_ollama=config.no_ollama,
        seen_keys=seen_keys,
        include_seen_undated=config.include_seen_undated,
        snapshot=snapshot,
    )
    aggregated.filter = filter_result

    grouping_result = stage_group(
        filter_result.dated_entries,
        filter_result.undated_entries,
        grouping,
        snapshot=snapshot,
        reddit_config=config.reddit if config.reddit_enabled else None,
    )
    aggregated.grouping = grouping_result

    # Summarise individual entries (flatten groups for plan, then rebuild).
    flat_dated = _flatten_items_to_entries(grouping_result.dated_items)
    flat_undated = _flatten_items_to_entries(grouping_result.undated_items)

    summarize_result = stage_summarize(
        config,
        flat_dated,
        flat_undated,
        profile_set,
        session.conn,
        allow_network=effective_run.allow_summary_network,
        quiet=run_options.quiet,
        snapshot=snapshot,
    )
    session.summarize_result = summarize_result
    aggregated.summarize = summarize_result
    aggregated.source_snapshot = snapshot  # type: ignore[attr-defined]

    # Rebuild item structure with summaries when grouping is active.
    if grouping.enabled and grouping_result.groups:
        dated_items = _rebuild_items_with_summaries(
            grouping_result.dated_items, list(summarize_result.dated_entries)
        )
        undated_items = _rebuild_items_with_summaries(
            grouping_result.undated_items, list(summarize_result.undated_entries)
        )
    else:
        dated_items = summarize_result.dated_entries
        undated_items = summarize_result.undated_entries

    all_rendered = list(summarize_result.dated_entries) + list(
        summarize_result.undated_entries
    )
    ollama_c, litellm_c, cache_c, fallback_c = count_summary_sources(all_rendered)
    meta = summarize_result.summary_metadata
    session.stats = DigestStats(
        folders_scanned=len(discovery.folders),
        messages_parsed=parse_result.counts.messages_parsed,
        dated_included=len(summarize_result.dated_entries),
        undated_needing_review=len(summarize_result.undated_entries),
        skipped_outside_window=filter_result.counts.skipped_outside_window,
        skipped_seen_undated=filter_result.counts.skipped_seen_undated,
        deduped_messages=filter_result.counts.deduped_messages,
        parse_errors=parse_result.counts.parse_fatal_errors,
        summaries_ollama=ollama_c,
        summaries_litellm=litellm_c,
        summaries_cache=cache_c,
        summaries_fallback=fallback_c,
        summaries_errors=meta.summaries_errors if meta else 0,
    )

    # Prefer grouped folder view when DigestGroup is available.
    dated_by_folder = _group_items_by_folder(dated_items)

    session.report = DigestReport(
        generated_at=generated_at,
        lookback_days=config.lookback_days,
        window_start=session.window_start,
        window_end=session.window_end,
        dated_by_folder=dated_by_folder,
        undated=tuple(
            _flatten_items_to_entries(undated_items)
            if grouping.enabled
            else undated_items
        ),
        stats=session.stats,
        summary_metadata=meta,
        grouping_metadata=_grouping_metadata(grouping_result)
        if grouping.enabled
        else None,
    )

    if effective_run.allow_group_summary_network:
        from rollup.group_summarize import apply_group_summaries

        new_dated, new_undated, gsm = apply_group_summaries(
            session.report.dated_by_folder,
            session.report.undated,
            config,
            session.conn,
            max_calls=config.max_group_summary_calls,
        )
        session.report = replace(
            session.report,
            dated_by_folder=new_dated,
            undated=new_undated,
            group_summary_metadata=gsm,
        )
        aggregated.group_summary_ollama_calls = gsm.ollama_calls
        aggregated.group_summary_cache_hits = gsm.cache_hits
        aggregated.group_summary_stream_failures = gsm.stream_failures
        aggregated.group_summary_cache_write_errors = gsm.cache_write_errors
        aggregated.group_summary_error_counts = dict(gsm.error_counts)
        if gsm.degraded or (
            gsm.groups_attempted > 0 and gsm.groups_succeeded == 0
        ):
            aggregated.group_summaries_degraded = True
            logger.warning(
                "Group summaries degraded: attempted=%d succeeded=%d "
                "errors=%d stream_failures=%d cache_write_errors=%d",
                gsm.groups_attempted,
                gsm.groups_succeeded,
                gsm.errors,
                gsm.stream_failures,
                gsm.cache_write_errors,
            )
    return None


def _emit_digest_artifacts(session: _DigestSession) -> DigestRunResult | None:
    """Archive, render, required writers, latest publish, seen state, manifest finalize."""
    config = session.config
    run_options = session.run_options
    aggregated = session.aggregated
    generated_at = session.generated_at
    ctx = session.ctx
    summarize_result = session.summarize_result
    assert summarize_result is not None
    assert session.stats is not None
    stats = session.stats
    report = session.report
    assert report is not None

    if run_options.dry_run:
        aggregated.usable_digest = True
        session.status = derive_run_status(aggregated, dry_run=True)
        if session.manifest_builder is not None and run_options.write_manifest:
            session.manifest_builder.finalize(
                status=session.status, aggregated=aggregated, stats=stats
            )
        return DigestRunResult(
            status=session.status,
            exit_code=status_to_exit_code(session.status),
            context=ctx,
            report=report,
            stats=stats,
            aggregated=aggregated,
        )

    # Keep only the new batch in output_dir root; prior digests → archive/.
    from rollup.output_archive import archive_previous_outputs

    archive_previous_outputs(config.output_dir, db_path=config.db_path)

    # Write outputs (variants or default).
    rendered_variants = summarize_result.rendered_variants
    execution = summarize_result.execution
    md_path = session.md_path
    html_path = session.html_path
    if rendered_variants and any(name != "default" for name in rendered_variants):
        for variant_name, (variant_dated, variant_undated) in rendered_variants.items():
            variant_metadata = (
                execution.summary_metadata_by_variant.get(variant_name)
                if execution
                else None
            )
            variant_stats = DigestStats(
                folders_scanned=stats.folders_scanned,
                messages_parsed=stats.messages_parsed,
                dated_included=len(variant_dated),
                undated_needing_review=len(variant_undated),
                skipped_outside_window=stats.skipped_outside_window,
                skipped_seen_undated=stats.skipped_seen_undated,
                deduped_messages=stats.deduped_messages,
                parse_errors=stats.parse_errors,
                summaries_ollama=(
                    variant_metadata.summaries_ollama if variant_metadata else 0
                ),
                summaries_cache=(
                    variant_metadata.summaries_cache if variant_metadata else 0
                ),
                summaries_fallback=(
                    variant_metadata.summaries_fallback if variant_metadata else 0
                ),
                summaries_errors=(
                    variant_metadata.summaries_errors if variant_metadata else 0
                ),
            )
            variant_report = DigestReport(
                generated_at=generated_at,
                lookback_days=config.lookback_days,
                window_start=session.window_start,
                window_end=session.window_end,
                dated_by_folder=group_dated_by_folder(variant_dated),
                undated=tuple(variant_undated),
                stats=variant_stats,
                summary_metadata=variant_metadata,
            )
            variant_report = _maybe_final_review(
                variant_report,
                config,
                session.conn,
                generated_at,
                variant_name,
                use_explicit_path=False,
                aggregated=aggregated,
                apply_policy=session.resolved_apply_policy,
                dry_run=run_options.dry_run,
                quiet=run_options.quiet,
            )
            stem = digest_output_stem(
                generated_at, variant_name, run_id_short=ctx.run_id_short
            )
            md = render_markdown(
                variant_report,
                config.max_display_links,
                folder_themes=config.folder_themes or None,
            )
            html_content = render_html(
                variant_report,
                config.max_display_links,
                folder_themes=config.folder_themes or None,
            )
            v_md, v_html = _write_digest_outputs(
                config.output_dir,
                generated_at,
                md,
                html_content,
                variant_name=variant_name,
                run_id_short=ctx.run_id_short,
            )
            aggregated.renders.append(
                RenderResult(
                    markdown=md,
                    html=html_content,
                    output_stem=stem,
                    variant_name=variant_name,
                    md_path=v_md,
                    html_path=v_html,
                )
            )
            if md_path is None:
                md_path, html_path = v_md, v_html
                report = variant_report
    else:
        stem = digest_output_stem(generated_at, run_id_short=ctx.run_id_short)
        report = _maybe_final_review(
            report,
            config,
            session.conn,
            generated_at,
            None,
            use_explicit_path=bool(config.final_review_report_path),
            aggregated=aggregated,
            apply_policy=session.resolved_apply_policy,
            dry_run=run_options.dry_run,
            quiet=run_options.quiet,
        )
        md = render_markdown(
            report,
            config.max_display_links,
            folder_themes=config.folder_themes or None,
        )
        html_content = render_html(
            report,
            config.max_display_links,
            folder_themes=config.folder_themes or None,
        )
        md_path, html_path = _write_digest_outputs(
            config.output_dir,
            generated_at,
            md,
            html_content,
            run_id_short=ctx.run_id_short,
        )
        aggregated.renders.append(
            RenderResult(
                markdown=md,
                html=html_content,
                output_stem=stem,
                md_path=md_path,
                html_path=html_path,
            )
        )

    session.report = report
    session.md_path = md_path
    session.html_path = html_path

    aggregated.dated_outputs_written = True
    aggregated.usable_digest = True
    aggregated.messages_included = len(summarize_result.dated_entries) + len(
        summarize_result.undated_entries
    )

    # Required output writers run before latest/seen/index (irreversible boundary).
    if (
        session.output_writers
        and session.report is not None
        and session.writer_cli_args is not None
    ):
        from rollup.output_writers import (
            OutputWriterError,
            WriteContext,
            run_enabled_writers,
        )

        try:
            written = run_enabled_writers(
                session.output_writers,
                session.report,
                WriteContext(
                    output_dir=config.output_dir,
                    generated_at=generated_at,
                    max_display_links=config.max_display_links,
                    dry_run=run_options.dry_run,
                    run_id_short=ctx.run_id_short,
                    logger=logger,
                    folder_themes=config.folder_themes or None,
                ),
                args=session.writer_cli_args,
                config=config,
            )
            aggregated.writer_artifacts.extend(written)
        except OutputWriterError as writer_exc:
            aggregated.required_writer_failed = True
            aggregated.hard_failure = True
            aggregated.hard_failure_reason = str(writer_exc)
            session.error_message = str(writer_exc)
            logger.error("Required output writer failed: %s", writer_exc)
            ctx.add_event("required_writer_failed", str(writer_exc), level="error")
            session.status = "failure"
            if session.manifest_builder is not None:
                try:
                    session.manifest_builder.record_failure(writer_exc)
                    session.manifest_builder.finalize(
                        status="failure", aggregated=aggregated
                    )
                except Exception:
                    pass
            return _failure_result(session)

    # Publish latest outputs transactionally when requested.
    # Dated digests are the durable source of truth; latest failure still
    # permits seen-state updates below (only after required pubs).
    session.status = derive_run_status(aggregated, dry_run=False)
    allow_latest = (
        run_options.publish_latest
        and session.md_path
        and session.html_path
        and aggregated.messages_included > 0
        and not aggregated.mbox_mutation_detected
        and not aggregated.required_writer_failed
    )
    if run_options.publish_latest and not allow_latest:
        if aggregated.messages_included == 0:
            ctx.add_event(
                "latest_skipped_empty_window",
                "Refusing latest.* update when messages_included == 0",
                level="info",
            )
        elif aggregated.mbox_mutation_detected:
            ctx.add_event(
                "latest_skipped_mbox_mutation",
                "Refusing latest.* update after mbox mutation",
                level="warning",
            )
    if allow_latest and session.md_path and session.html_path:
        from rollup.publication import publish_latest_outputs

        try:
            pub = publish_latest_outputs(
                output_dir=config.output_dir,
                md_path=session.md_path,
                html_path=session.html_path,
                run_status=session.status,
                publish_latest=run_options.publish_latest,
                allow_partial_latest=run_options.allow_partial_latest,
            )
            aggregated.latest_outputs_updated = pub.latest_outputs_updated
        except OSError as pub_exc:
            aggregated.publication_failed = True
            logger.error("Latest publication failed: %s", pub_exc)
            ctx.add_event(
                "publication_failed",
                str(pub_exc),
                level="error",
            )
        except (ValueError, FileNotFoundError) as pub_exc:
            aggregated.publication_failed = True
            logger.error("Latest publication failed: %s", pub_exc)
            ctx.add_event(
                "publication_failed",
                str(pub_exc),
                level="error",
            )

    if session.conn is not None:
        from rollup.state import upsert_seen_keys

        rendered_undated_keys = [
            e.classified.parsed.message_key
            for e in summarize_result.undated_entries
        ]
        try:
            upsert_seen_keys(session.conn, rendered_undated_keys, generated_at)
            aggregated.seen_state_updated = True
        except Exception as seen_exc:
            # Digest exists; safe consequence is repetition → partial.
            aggregated.seen_state_failed = True
            logger.error("Seen-state update failed: %s", seen_exc)
            ctx.add_event(
                "seen_state_failed",
                str(seen_exc),
                level="error",
            )

        if (
            aggregated.webpage_queue_ingest
            and aggregated.dated_outputs_written
            and summarize_result is not None
        ):
            from rollup.webpage.queue import mark_ingested as mark_webpage_ingested

            included_keys = {
                e.classified.parsed.message_key
                for e in summarize_result.dated_entries
            } | {
                e.classified.parsed.message_key
                for e in summarize_result.undated_entries
            }
            to_mark = [
                (qid, mk, session.ctx.run_id)
                for qid, mk in aggregated.webpage_queue_ingest
                if mk in included_keys
            ]
            if to_mark:
                try:
                    mark_webpage_ingested(
                        session.conn, to_mark, ingested_at=generated_at
                    )
                except Exception as web_exc:
                    aggregated.webpage_degraded = True
                    logger.error("Webpage queue mark-ingested failed: %s", web_exc)
                    ctx.add_event(
                        "webpage_queue_ingest_failed",
                        str(web_exc),
                        level="error",
                    )

    session.status = derive_run_status(aggregated, dry_run=False)
    if session.manifest_builder is not None:
        session.manifest_builder.set_outputs(
            md_path=session.md_path,
            html_path=session.html_path,
            dated_outputs_written=aggregated.dated_outputs_written,
            latest_outputs_updated=aggregated.latest_outputs_updated,
        )
        session.manifest_builder.finalize(
            status=session.status,
            aggregated=aggregated,
            stats=session.stats,
            report=session.report,
        )
    return None


def _release_resources(session: _DigestSession) -> None:
    if session.conn is not None:
        try:
            session.conn.close()
        except Exception:
            pass
    if session.lock is not None:
        try:
            session.lock.release()
        except Exception as exc:
            logger.warning("Failed to release run lock: %s", exc)
    if session.manifest_builder is not None:
        try:
            written = session.manifest_builder.write_if_state_writable(
                update_latest=session.aggregated.latest_outputs_updated
                and session.status == "success"
            )
            if written is not None:
                session.manifest_path = written
        except Exception as manifest_exc:
            session.secondary_manifest_error = str(manifest_exc)
            session.aggregated.manifest_write_failed = True
            logger.error("Manifest write failed: %s", manifest_exc)
            if session.status in ("success", "partial", "dry_run"):
                session.status = derive_run_status(
                    session.aggregated, dry_run=False
                )


def _index_web_run(session: _DigestSession) -> None:
    run_options = session.run_options
    aggregated = session.aggregated
    if (
        not run_options.dry_run
        and aggregated.dated_outputs_written
        and session.status in ("success", "partial")
        and session.report is not None
        and session.md_path is not None
        and session.html_path is not None
    ):
        manifests_required = run_options.write_manifest
        manifests_ok = (not manifests_required) or (
            session.manifest_path is not None
            and not aggregated.manifest_write_failed
        )
        if manifests_ok:
            try:
                from rollup import __version__
                from rollup.run_index import build_pipeline_payload, index_rollup_run
                from rollup.utc import now_utc

                payload = build_pipeline_payload(
                    run_id=session.ctx.run_id,
                    report=session.report,
                    status=session.status,
                    mode=run_options.mode,
                    rollup_version=__version__,
                    started_at=session.ctx.run_start_time,
                    completed_at=now_utc(),
                    md_path=session.md_path,
                    html_path=session.html_path,
                    manifest_path=session.manifest_path,
                    output_dir=session.config.output_dir,
                    state_dir=session.config.state_dir,
                    aggregated=aggregated,
                    max_display_links=session.config.max_display_links,
                )
                index_rollup_run(session.config.db_path, payload)
            except Exception as index_exc:
                aggregated.web_index_failed = True
                aggregated.web_index_error = str(index_exc)
                logger.error("Web run index failed: %s", index_exc)
                session.ctx.add_event(
                    "web_index_failed", str(index_exc), level="error"
                )
        else:
            logger.warning(
                "Skipping web run index: manifest required but missing/failed"
            )


def run_digest(
    config: Config,
    run_options: RunOptions,
    *,
    grouping: GroupingConfig | None = None,
    manifest_config: ManifestConfig | None = None,
    clock: Clock | None = None,
    acquire_lock: bool = True,
    output_writers: list | None = None,
    writer_cli_args: object | None = None,
) -> DigestRunResult:
    """Run the full digest pipeline with typed stage results."""
    clock = clock or DEFAULT_CLOCK
    grouping = grouping or GroupingConfig(enabled=False)
    manifest_config = manifest_config or ManifestConfig(
        manifest_dir=config.state_dir / "manifests"
    )
    ctx = RunContext.create(mode=run_options.mode, clock=clock)
    generated_at = ctx.run_start_time
    aggregated = AggregatedResults(ollama_enabled=config.llm_enabled)
    window_start, window_end = compute_date_window(
        generated_at, config.lookback_days
    )

    effective_run = resolve_effective_run(config, run_options, grouping=grouping)
    resolved_apply_policy = effective_run.apply_policy

    session = _DigestSession(
        config=config,
        run_options=run_options,
        grouping=grouping,
        manifest_config=manifest_config,
        acquire_lock=acquire_lock,
        output_writers=output_writers,
        writer_cli_args=writer_cli_args,
        ctx=ctx,
        generated_at=generated_at,
        aggregated=aggregated,
        window_start=window_start,
        window_end=window_end,
        effective_run=effective_run,
        resolved_apply_policy=resolved_apply_policy,
    )

    early = _validate_run_paths(session)
    if early is not None:
        return early

    try:
        early = _prepare_lock_and_manifest(session)
        if early is not None:
            return early

        early = _run_core_stages(session)
        if early is not None:
            return early

        early = _emit_digest_artifacts(session)
        if early is not None:
            return early

    except Exception as exc:
        session.aggregated.hard_failure = True
        if session.error_message is None:
            session.error_message = str(exc)
        logger.error("Digest failed: %s", exc)
        session.status = "failure"
        if session.manifest_builder is not None:
            try:
                session.manifest_builder.record_failure(exc)
                session.manifest_builder.finalize(
                    status="failure",
                    aggregated=session.aggregated,
                    stats=session.stats,
                )
            except Exception:
                pass
    finally:
        _release_resources(session)

    _index_web_run(session)

    return DigestRunResult(
        status=session.status,
        exit_code=status_to_exit_code(session.status),
        context=session.ctx,
        report=session.report,
        stats=session.stats,
        aggregated=session.aggregated,
        md_path=session.md_path,
        html_path=session.html_path,
        manifest_path=session.manifest_path,
        secondary_manifest_error=session.secondary_manifest_error,
        error_message=session.error_message,
    )



def _write_digest_outputs(
    output_dir: Path,
    generated_at: datetime,
    markdown: str,
    html_content: str,
    *,
    variant_name: str | None = None,
    run_id_short: str | None = None,
) -> tuple[Path, Path]:
    """Write digest MD/HTML via the supported atomic_write_digest API."""
    return atomic_write_digest(
        output_dir,
        generated_at,
        markdown,
        html_content,
        variant_name=variant_name,
        run_id_short=run_id_short,
    )


def _maybe_final_review(
    report: DigestReport,
    config: Config,
    conn,
    generated_at: datetime,
    variant_name: str | None,
    *,
    use_explicit_path: bool,
    aggregated: AggregatedResults,
    apply_policy=None,
    dry_run: bool = False,
    quiet: bool = True,
) -> DigestReport:
    if not config.final_review_enabled or dry_run:
        return report
    from rollup.final_review import (
        execute_final_review,
        print_final_review_summary,
        write_final_review_report,
    )

    stem = digest_output_stem(generated_at, variant_name)

    if use_explicit_path and config.final_review_report_path:
        report_path = config.final_review_report_path
    else:
        report_path = config.output_dir / f"{stem}.final-review.json"
    result = execute_final_review(report, config, conn=conn, quiet=quiet)
    try:
        write_final_review_report(result, report_path)
    except OSError as sidecar_exc:
        # Sidecar is not part of the dated-digest transaction → partial.
        logger.error("Final-review sidecar write failed: %s", sidecar_exc)
        aggregated.final_review_failed = True
    else:
        print_final_review_summary(result, report_path)
    if result.overall_status == "fail":
        aggregated.final_review_failed = True

    report = replace(report, final_review=result)

    if config.final_review_mode == "apply":
        from rollup.final_review_apply import apply_final_review_patches
        from rollup.final_review_codes import resolve_apply_policy

        policy = apply_policy
        if policy is None:
            policy = resolve_apply_policy(
                cron=False,
                apply_policy_name=config.final_review_apply_policy,
                allow_cron_apply=config.final_review_allow_cron_apply,
                max_patches_unattended=config.final_review_max_patches_unattended,
                max_changed_chars_unattended=config.final_review_max_changed_chars_unattended,
                max_changed_chars_ratio=config.final_review_max_changed_chars_ratio,
                preserve_links=config.final_review_preserve_links,
                preserve_quotes=config.final_review_preserve_quotes,
            )

        report, apply_result = apply_final_review_patches(
            report, result, config, policy=policy
        )
        aggregated.apply_patches_applied = apply_result.applied
        aggregated.apply_patches_attempted = apply_result.attempted
        aggregated.apply_global_skip_reason = apply_result.global_skip_reason
        aggregated.apply_reject_counts = dict(apply_result.reject_counts)
        aggregated.apply_policy_unattended = policy.unattended
        aggregated.apply_policy_max_patches = policy.max_patches_unattended
        aggregated.apply_policy_max_changed_chars = policy.max_changed_chars_unattended
        if apply_result.applied > 0:
            aggregated.contains_auto_edited_prose = True
            logger.info(
                "Final review apply: applied=%d rejected=%d",
                apply_result.applied,
                apply_result.rejected,
            )
        elif apply_result.global_skip_reason:
            logger.info(
                "Final review apply skipped: %s",
                apply_result.global_skip_reason,
            )
        elif apply_result.rejected:
            logger.info(
                "Final review apply: applied=0 rejected=%d codes=%s",
                apply_result.rejected,
                dict(apply_result.reject_counts),
            )

    return report


def _group_items_by_folder(items: tuple[Any, ...]) -> dict:
    """Group DigestEntry or DigestGroup items by folder name."""
    from rollup.models import DigestEntry as DE

    folders: dict[str, list] = {}
    for item in items:
        if isinstance(item, DE):
            folder = item.classified.parsed.folder_name
        elif hasattr(item, "folder_name"):
            folder = item.folder_name
        else:
            folder = "unknown"
        folders.setdefault(folder, []).append(item)
    # Convert to tuples; DigestReport still types as DigestEntry — cast via Any.
    return {k: tuple(v) for k, v in sorted(folders.items())}


def _grouping_metadata(grouping_result: GroupingResult):
    try:
        from rollup.models import GroupingMetadata

        counts: dict[str, int] = {}
        for g in grouping_result.groups:
            counts[g.group_type] = counts.get(g.group_type, 0) + 1
        return GroupingMetadata(
            groups_created=len(grouping_result.groups),
            messages_in_groups=sum(len(g.entries) for g in grouping_result.groups),
            standalone_cards=sum(
                1
                for i in list(grouping_result.dated_items)
                + list(grouping_result.undated_items)
                if not hasattr(i, "entries")
            ),
            grouping_counts=counts,
        )
    except Exception:
        return None
