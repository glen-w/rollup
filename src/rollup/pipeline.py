"""Digest pipeline orchestration with typed stage results."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

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
from rollup.summary_profiles import (
    get_canonical_newsletter_types,
    load_summary_profile_set,
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
    warnings: tuple[StageWarning, ...] = ()


@dataclass(frozen=True)
class ParseResult:
    messages: tuple[ParsedMessage, ...]
    counts: ParseCounts
    warnings: tuple[StageWarning, ...] = ()
    errors: tuple[StageError, ...] = ()


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

    if aggregated.ollama_enabled and aggregated.summarize:
        meta = aggregated.summarize.summary_metadata
        if meta is not None:
            total = (
                meta.summaries_ollama
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

    return "success"


def status_to_exit_code(status: RunStatus) -> int:
    if status in ("success", "dry_run"):
        return EXIT_SUCCESS
    if status == "partial":
        return EXIT_PARTIAL
    return EXIT_FAILURE


def stage_discover(config: Config) -> DiscoveryResult:
    folders = list(iter_mbox_files(config.root))
    folders = filter_folders(folders, config.folders_include, config.folders_exclude)
    return DiscoveryResult(folders=tuple(folders))


def stage_parse(config: Config, folders: tuple[MboxFolder, ...]) -> ParseResult:
    from rollup.parse import parse_mbox_folder

    messages: list[ParsedMessage] = []
    warnings: list[StageWarning] = []
    errors: list[StageError] = []
    seen = 0
    fatal = 0
    anomalies = 0
    folders_failed = 0

    for folder in folders:
        logger.info("Parsing %s (%s)", folder.folder_name, folder.mbox_path)
        msgs, err_count, folder_errors = parse_mbox_folder(
            folder, config.max_body_chars, config.max_display_links
        )
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
) -> GroupingResult:
    """Apply grouping when enabled; otherwise pass entries through as items."""
    if not grouping.enabled:
        return GroupingResult(
            dated_items=dated_entries,
            undated_items=undated_entries,
        )
    from rollup.grouping import apply_grouping

    applied = apply_grouping(
        dated_entries, undated_entries, grouping, snapshot=snapshot
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


def run_digest(
    config: Config,
    run_options: RunOptions,
    *,
    grouping: GroupingConfig | None = None,
    manifest_config: ManifestConfig | None = None,
    clock: Clock | None = None,
    acquire_lock: bool = True,
) -> DigestRunResult:
    """Run the full digest pipeline with typed stage results."""
    clock = clock or DEFAULT_CLOCK
    grouping = grouping or GroupingConfig(enabled=False)
    manifest_config = manifest_config or ManifestConfig(
        manifest_dir=config.state_dir / "manifests"
    )
    ctx = RunContext.create(mode=run_options.mode, clock=clock)
    generated_at = ctx.run_start_time
    aggregated = AggregatedResults(ollama_enabled=not config.no_ollama)
    window_start, window_end = compute_date_window(
        generated_at, config.lookback_days
    )

    effective_run = resolve_effective_run(config, run_options, grouping=grouping)
    resolved_apply_policy = effective_run.apply_policy

    lock = None
    conn = None
    report: DigestReport | None = None
    stats: DigestStats | None = None
    md_path: Path | None = None
    html_path: Path | None = None
    manifest_path: Path | None = None
    secondary_manifest_error: str | None = None
    error_message: str | None = None
    status: RunStatus = "failure"

    from rollup.manifest import ManifestBuilder
    from rollup.run_lock import RunLockError, acquire_run_lock

    manifest_builder = None
    if run_options.write_manifest and not run_options.dry_run:
        manifest_builder = ManifestBuilder(
            ctx,
            config=config,
            run_options=run_options,
            grouping=grouping,
            manifest_config=manifest_config,
            window_start=window_start,
            window_end=window_end,
        )

    try:
        if acquire_lock and not run_options.dry_run:
            try:
                lock = acquire_run_lock(
                    config.state_dir, ctx.run_id, started_at=generated_at
                )
                if getattr(lock, "stale_recovered", False):
                    ctx.add_event(
                        "stale_lock_recovered",
                        "Recovered stale run lock",
                        level="warning",
                    )
            except RunLockError as exc:
                aggregated.hard_failure = True
                aggregated.hard_failure_reason = str(exc)
                error_message = str(exc)
                status = "failure"
                if manifest_builder is not None:
                    manifest_builder.record_failure(exc)
                    manifest_builder.finalize(status="failure", aggregated=aggregated)
                return DigestRunResult(
                    status=status,
                    exit_code=EXIT_FAILURE,
                    context=ctx,
                    report=None,
                    stats=None,
                    aggregated=aggregated,
                    error_message=error_message,
                )

        profile_set = require_valid_summary_profile_set(
            load_summary_profile_set(config.summary_profile_set_path),
            get_canonical_newsletter_types(),
        )

        if effective_run.allow_summary_network:
            from rollup.summarize import OllamaError, validate_ollama_url

            try:
                validate_ollama_url(config.ollama_url, config.allow_remote_ollama)
            except OllamaError as exc:
                aggregated.hard_failure = True
                error_message = str(exc)
                raise

        if effective_run.allow_final_review_network:
            from rollup.summarize import OllamaError, validate_ollama_url

            try:
                validate_ollama_url(config.ollama_url, config.allow_remote_ollama)
            except OllamaError as exc:
                aggregated.hard_failure = True
                error_message = str(exc)
                raise

        discovery = stage_discover(config)
        aggregated.discovery = discovery
        logger.info(
            "Digest: root=%s folders=%d lookback=%dd dry_run=%s no_ollama=%s",
            config.root,
            len(discovery.folders),
            config.lookback_days,
            run_options.dry_run,
            config.no_ollama,
        )

        parse_result = stage_parse(config, discovery.folders)
        aggregated.parse = parse_result

        seen_keys: set[str] = set()
        snapshot = None
        if not run_options.dry_run:
            from rollup.source_models import empty_defaults_snapshot
            from rollup.source_registry import (
                load_SourceRegistrySnapshot,
                observe_sources,
            )
            from rollup.state import ensure_final_review_schema, init_db, load_seen_keys

            if not config.no_ollama:
                from rollup.state import init_db_with_summaries

                conn = init_db_with_summaries(config.db_path)
            else:
                conn = init_db(config.db_path)
                if config.final_review_enabled:
                    ensure_final_review_schema(conn)
                if config.group_summaries_enabled:
                    from rollup.state import ensure_group_summary_schema

                    ensure_group_summary_schema(conn)
            seen_keys = load_seen_keys(conn)
            observe_result = observe_sources(
                conn, parse_result.messages, generated_at=generated_at
            )
            needed = {
                m.source_key for m in parse_result.messages if m.source_key
            }
            snapshot = load_SourceRegistrySnapshot(
                conn,
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
            conn,
            allow_network=effective_run.allow_summary_network,
            quiet=run_options.quiet,
            snapshot=snapshot,
        )
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
        ollama_c, cache_c, fallback_c = count_summary_sources(all_rendered)
        meta = summarize_result.summary_metadata
        stats = DigestStats(
            folders_scanned=len(discovery.folders),
            messages_parsed=parse_result.counts.messages_parsed,
            dated_included=len(summarize_result.dated_entries),
            undated_needing_review=len(summarize_result.undated_entries),
            skipped_outside_window=filter_result.counts.skipped_outside_window,
            skipped_seen_undated=filter_result.counts.skipped_seen_undated,
            deduped_messages=filter_result.counts.deduped_messages,
            parse_errors=parse_result.counts.parse_fatal_errors,
            summaries_ollama=ollama_c,
            summaries_cache=cache_c,
            summaries_fallback=fallback_c,
            summaries_errors=meta.summaries_errors if meta else 0,
        )

        # Prefer grouped folder view when DigestGroup is available.
        dated_by_folder = _group_items_by_folder(dated_items)

        report = DigestReport(
            generated_at=generated_at,
            lookback_days=config.lookback_days,
            window_start=window_start,
            window_end=window_end,
            dated_by_folder=dated_by_folder,
            undated=tuple(
                _flatten_items_to_entries(undated_items)
                if grouping.enabled
                else undated_items
            ),
            stats=stats,
            summary_metadata=meta,
            grouping_metadata=_grouping_metadata(grouping_result)
            if grouping.enabled
            else None,
        )

        if effective_run.allow_group_summary_network:
            from rollup.group_summarize import apply_group_summaries

            new_dated, new_undated, gsm = apply_group_summaries(
                report.dated_by_folder,
                report.undated,
                config,
                conn,
                max_calls=config.max_group_summary_calls,
            )
            report = replace(
                report,
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

        if run_options.dry_run:
            aggregated.usable_digest = True
            status = derive_run_status(
                aggregated, dry_run=True
            )
            if manifest_builder is not None and run_options.write_manifest:
                manifest_builder.finalize(status=status, aggregated=aggregated, stats=stats)
            return DigestRunResult(
                status=status,
                exit_code=status_to_exit_code(status),
                context=ctx,
                report=report,
                stats=stats,
                aggregated=aggregated,
            )

        # Write outputs (variants or default).
        rendered_variants = summarize_result.rendered_variants
        execution = summarize_result.execution
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
                    window_start=window_start,
                    window_end=window_end,
                    dated_by_folder=group_dated_by_folder(variant_dated),
                    undated=tuple(variant_undated),
                    stats=variant_stats,
                    summary_metadata=variant_metadata,
                )
                variant_report = _maybe_final_review(
                    variant_report,
                    config,
                    conn,
                    generated_at,
                    variant_name,
                    use_explicit_path=False,
                    aggregated=aggregated,
                    apply_policy=resolved_apply_policy,
                    dry_run=run_options.dry_run,
                    quiet=run_options.quiet,
                )
                stem = digest_output_stem(
                    generated_at, variant_name, run_id_short=ctx.run_id_short
                )
                md = render_markdown(variant_report, config.max_display_links)
                html_content = render_html(variant_report, config.max_display_links)
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
            stem = digest_output_stem(
                generated_at, run_id_short=ctx.run_id_short
            )
            report = _maybe_final_review(
                report,
                config,
                conn,
                generated_at,
                None,
                use_explicit_path=bool(config.final_review_report_path),
                aggregated=aggregated,
                apply_policy=resolved_apply_policy,
                dry_run=run_options.dry_run,
                quiet=run_options.quiet,
            )
            md = render_markdown(report, config.max_display_links)
            html_content = render_html(report, config.max_display_links)
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

        aggregated.dated_outputs_written = True
        aggregated.usable_digest = True

        # Publish latest outputs transactionally when requested.
        # Dated digests are the durable source of truth; latest failure still
        # permits seen-state updates below.
        status = derive_run_status(aggregated, dry_run=False)
        if run_options.publish_latest and md_path and html_path:
            from rollup.publication import publish_latest_outputs

            try:
                pub = publish_latest_outputs(
                    output_dir=config.output_dir,
                    md_path=md_path,
                    html_path=html_path,
                    run_status=status,
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

        if conn is not None:
            from rollup.state import upsert_seen_keys

            rendered_undated_keys = [
                e.classified.parsed.message_key
                for e in summarize_result.undated_entries
            ]
            try:
                upsert_seen_keys(conn, rendered_undated_keys, generated_at)
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

        status = derive_run_status(aggregated, dry_run=False)
        if manifest_builder is not None:
            manifest_builder.set_outputs(
                md_path=md_path,
                html_path=html_path,
                dated_outputs_written=aggregated.dated_outputs_written,
                latest_outputs_updated=aggregated.latest_outputs_updated,
            )
            manifest_builder.finalize(
                status=status, aggregated=aggregated, stats=stats, report=report
            )

    except Exception as exc:
        aggregated.hard_failure = True
        if error_message is None:
            error_message = str(exc)
        logger.error("Digest failed: %s", exc)
        status = "failure"
        if manifest_builder is not None:
            try:
                manifest_builder.record_failure(exc)
                manifest_builder.finalize(
                    status="failure", aggregated=aggregated, stats=stats
                )
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if lock is not None:
            try:
                lock.release()
            except Exception as exc:
                logger.warning("Failed to release run lock: %s", exc)
        if manifest_builder is not None:
            try:
                written = manifest_builder.write_if_state_writable(
                    update_latest=aggregated.latest_outputs_updated
                    and status == "success"
                )
                if written is not None:
                    manifest_path = written
            except Exception as manifest_exc:
                secondary_manifest_error = str(manifest_exc)
                aggregated.manifest_write_failed = True
                logger.error("Manifest write failed: %s", manifest_exc)
                if status in ("success", "partial", "dry_run"):
                    status = derive_run_status(aggregated, dry_run=False)

    return DigestRunResult(
        status=status,
        exit_code=status_to_exit_code(status),
        context=ctx,
        report=report,
        stats=stats,
        aggregated=aggregated,
        md_path=md_path,
        html_path=html_path,
        manifest_path=manifest_path,
        secondary_manifest_error=secondary_manifest_error,
        error_message=error_message,
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
    """Write digest using run_id-aware stem when supported."""
    try:
        return atomic_write_digest(
            output_dir,
            generated_at,
            markdown,
            html_content,
            variant_name=variant_name,
            run_id_short=run_id_short,
        )
    except TypeError:
        return atomic_write_digest(
            output_dir,
            generated_at,
            markdown,
            html_content,
            variant_name=variant_name,
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

    try:
        stem = digest_output_stem(
            generated_at, variant_name
        )
    except TypeError:
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
