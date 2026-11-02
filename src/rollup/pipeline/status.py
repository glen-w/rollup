"""Run status derivation and the no-input gate."""

from __future__ import annotations

from rollup.pipeline.types import (
    DEFAULT_DEGRADATION_POLICY,
    AggregatedResults,
    DegradationPolicy,
    DiscoveryResult,
    ParseResult,
)
from rollup.run_context import RunStatus
from rollup.run_contracts import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS

_PARTIAL_REASONS: tuple[tuple[str, str], ...] = (
    ("mbox_mutation_detected", "mbox mutation"),
    ("optional_writer_failed", "optional writer"),
    ("archive_failed", "output archive"),
    ("publication_failed", "latest publication"),
    ("seen_state_failed", "seen-state"),
    ("manifest_write_failed", "manifest"),
    ("final_review_failed", "final review"),
    ("group_summaries_degraded", "group summaries"),
    ("linkedin_degraded", "linkedin"),
    ("reddit_degraded", "reddit"),
    ("webpage_degraded", "webpage"),
    ("gmail_degraded", "gmail"),
    ("rss_degraded", "rss"),
    ("continuity_ledger_failed", "continuity ledger"),
    ("web_index_failed", "web index"),
)


def format_partial_reasons(aggregated: AggregatedResults) -> str:
    """Compact cron-friendly list of why a run is partial (or degraded)."""
    labels = [
        label for attr, label in _PARTIAL_REASONS if getattr(aggregated, attr, False)
    ]
    return ", ".join(labels)


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

    if aggregated.archive_failed:
        return "partial"

    if aggregated.mbox_mutation_detected:
        return "partial"

    if aggregated.linkedin_degraded:
        return "partial"

    if aggregated.reddit_degraded:
        return "partial"

    if aggregated.webpage_degraded:
        return "partial"

    if aggregated.gmail_degraded:
        return "partial"

    if aggregated.rss_degraded:
        return "partial"

    if aggregated.continuity_ledger_failed:
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
    has_gmail = bool(discovery.gmail_targets)
    has_rss = bool(discovery.rss_feeds)
    has_any = has_mbox or has_linkedin or has_reddit or has_webpage or has_gmail or has_rss

    if folders_include and not has_any:
        return (
            "No folders matched explicit include "
            f"({', '.join(folders_include)}); refusing to publish"
        )
    if not has_any:
        return (
            "No readable mbox folders, LinkedIn searches, Reddit subs, Gmail labels, "
            "RSS feeds, or webpage queue items; refusing to publish"
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

    if (
        has_gmail
        and not (has_mbox or has_linkedin or has_webpage or has_reddit or has_rss)
        and parse.counts.messages_parsed == 0
    ):
        gmail_failed = any(
            w.code.startswith("gmail_") and w.code != "gmail_dry_run"
            for w in parse.warnings
        )
        if gmail_failed:
            return (
                "Gmail fetch failed and no other input sources; "
                "refusing to publish"
            )

    if (
        has_rss
        and not (has_mbox or has_linkedin or has_webpage or has_reddit or has_gmail)
        and parse.counts.messages_parsed == 0
    ):
        rss_failed = any(
            w.code.startswith("rss_") and w.code != "rss_dry_run"
            for w in parse.warnings
        )
        if rss_failed:
            return (
                "RSS fetch failed and no other input sources; "
                "refusing to publish"
            )

    return None
