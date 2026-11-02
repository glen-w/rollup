"""Digest pipeline orchestration with typed stage results."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from rollup.clock import DEFAULT_CLOCK, Clock
from rollup.config import Config, compute_date_window
from rollup.effective_run import resolve_effective_run
from rollup.pipeline.ingest import (
    merge_gmail_parse,
    merge_linkedin_parse,
    merge_parse,
    merge_reddit_parse,
    merge_webpage_parse,
    stage_discover,
    stage_enrich_scholar,
    stage_parse,
    stage_parse_gmail,
    stage_parse_linkedin,
    stage_parse_mbox,
    stage_parse_reddit,
    stage_parse_webpage,
)
from rollup.pipeline.publish import (
    _emit_digest_artifacts,
    _index_web_run,
)
from rollup.pipeline.session import (
    _DigestSession,
    _prepare_lock_and_manifest,
    _release_resources,
    _validate_run_paths,
)
from rollup.pipeline.stages import (
    _run_core_stages,
    build_digest_report,
    stage_filter,
    stage_group,
    stage_summarize,
)
from rollup.pipeline.status import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    derive_run_status,
    evaluate_no_input,
    format_partial_reasons,
    status_to_exit_code,
)
from rollup.pipeline.types import (
    DEFAULT_DEGRADATION_POLICY,
    AggregatedResults,
    DegradationPolicy,
    DigestRunResult,
    DiscoveryResult,
    FilterCounts,
    FilterResult,
    GroupingResult,
    ParseCounts,
    ParseResult,
    RenderResult,
    ReviewResult,
    StageError,
    StageWarning,
    SummarizeResult,
)

# Patch seam: publish.py resolves this name through the package at call time.
from rollup.render import atomic_write_digest as atomic_write_digest
from rollup.run_context import RunContext
from rollup.run_options import GroupingConfig, ManifestConfig, RunOptions

if TYPE_CHECKING:
    from rollup.output_writers import OutputWriter

logger = logging.getLogger(__name__)

__all__ = (
    "EXIT_FAILURE",
    "EXIT_PARTIAL",
    "EXIT_SUCCESS",
    "AggregatedResults",
    "DEFAULT_DEGRADATION_POLICY",
    "DegradationPolicy",
    "DigestRunResult",
    "DiscoveryResult",
    "FilterCounts",
    "FilterResult",
    "GroupingResult",
    "ParseCounts",
    "ParseResult",
    "RenderResult",
    "ReviewResult",
    "StageError",
    "StageWarning",
    "SummarizeResult",
    "build_digest_report",
    "derive_run_status",
    "evaluate_no_input",
    "format_partial_reasons",
    "merge_gmail_parse",
    "merge_linkedin_parse",
    "merge_parse",
    "merge_reddit_parse",
    "merge_webpage_parse",
    "run_digest",
    "stage_discover",
    "stage_enrich_scholar",
    "stage_filter",
    "stage_group",
    "stage_parse",
    "stage_parse_mbox",
    "stage_parse_gmail",
    "stage_parse_linkedin",
    "stage_parse_reddit",
    "stage_parse_webpage",
    "stage_summarize",
    "status_to_exit_code",
)


def run_digest(
    config: Config,
    run_options: RunOptions,
    *,
    grouping: GroupingConfig | None = None,
    manifest_config: ManifestConfig | None = None,
    clock: Clock | None = None,
    acquire_lock: bool = True,
    output_writers: Mapping[str, OutputWriter] | None = None,
    writer_cli_args: argparse.Namespace | None = None,
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
            except Exception as manifest_exc:
                session.aggregated.manifest_write_failed = True
                session.secondary_manifest_error = str(manifest_exc)
                logger.error(
                    "Manifest finalize after failure also failed: %s",
                    manifest_exc,
                )
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



