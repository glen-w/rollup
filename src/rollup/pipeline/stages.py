"""Core digest stages between ingest and publication."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any

from rollup.config import Config
from rollup.effort import resolve_profile_set
from rollup.filter import (
    apply_undated_seen_filter,
    build_digest_entries,
    count_summary_sources,
    group_dated_by_folder,
)
from rollup.models import DigestEntry, DigestReport, DigestStats, DigestSummaryMetadata
from rollup.pipeline.ingest import (
    merge_linkedin_parse,
    merge_reddit_parse,
    merge_webpage_parse,
    stage_discover,
    stage_enrich_scholar,
    stage_parse,
    stage_parse_linkedin,
    stage_parse_reddit,
    stage_parse_webpage,
)
from rollup.pipeline.session import _DigestSession, _failure_result
from rollup.pipeline.status import evaluate_no_input
from rollup.pipeline.types import (
    DigestRunResult,
    FilterCounts,
    FilterResult,
    GroupingResult,
    SummarizeResult,
)
from rollup.run_options import GroupingConfig
from rollup.summary_plan import SummaryCliOptions, resolve_summary_plan
from rollup.summary_profiles import (
    get_canonical_newsletter_types,
    require_valid_summary_profile_set,
)

logger = logging.getLogger(__name__)


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
    if discovery.reddit_subs:
        from rollup.reddit.cache import count_subs_needing_fetch
        from rollup.reddit.fetch import reddit_fetch_eta_log_line
        from rollup.state import init_db

        when = generated_at or datetime.now().astimezone()
        conn = init_db(config.db_path)
        try:
            fetch_count = count_subs_needing_fetch(
                conn,
                discovery.reddit_subs,
                config=config.reddit,
                lookback_days=config.lookback_days,
                ttl_hours=config.reddit.fetch_ttl_hours,
                refresh=config.reddit_refresh,
                now=when,
            )
        finally:
            conn.close()
        if fetch_count:
            logger.info(
                "%s",
                reddit_fetch_eta_log_line(
                    fetch_count,
                    prefix="Reddit fetch estimate",
                ),
            )
        elif config.reddit.fetch_ttl_hours > 0:
            logger.info(
                "Reddit fetch estimate: all %d subs served from cache",
                len(discovery.reddit_subs),
            )

    parse_result = stage_parse(config, discovery.folders)
    reddit_subs = discovery.reddit_subs if (
        effective_run.allow_reddit_network or run_options.dry_run
    ) else ()
    rd_messages, rd_warnings, reddit_degraded = stage_parse_reddit(
        config,
        reddit_subs,
        dry_run=run_options.dry_run,
        generated_at=generated_at,
    )
    parse_result = merge_reddit_parse(parse_result, rd_messages, rd_warnings)
    aggregated.reddit_degraded = reddit_degraded
    linkedin_searches = discovery.linkedin_searches if (
        effective_run.allow_linkedin_network or run_options.dry_run
    ) else ()
    li_messages, li_warnings, linkedin_degraded = stage_parse_linkedin(
        config,
        linkedin_searches,
        dry_run=run_options.dry_run,
        generated_at=generated_at,
    )
    parse_result = merge_linkedin_parse(parse_result, li_messages, li_warnings)
    aggregated.linkedin_degraded = linkedin_degraded
    wp_items = discovery.webpage_items if (
        effective_run.allow_webpage_network or run_options.dry_run
    ) else ()
    wp_messages, wp_warnings, webpage_degraded, wp_ingest = stage_parse_webpage(
        config,
        wp_items,
        dry_run=run_options.dry_run,
        generated_at=generated_at,
    )
    parse_result = merge_webpage_parse(parse_result, wp_messages, wp_warnings)
    aggregated.webpage_degraded = webpage_degraded
    aggregated.webpage_queue_ingest = wp_ingest
    parse_result = stage_enrich_scholar(
        config,
        parse_result,
        allow_network=effective_run.allow_scholar_network,
        dry_run=run_options.dry_run,
        generated_at=generated_at,
    )
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
            need_ollama_url = effective_run.allow_summary_network or (
                effective_run.allow_final_review_network
                and config.final_review_provider == "ollama"
            )
            if need_ollama_url:
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
