"""Publication boundary: render, writers, latest.*, seen-state, web index."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from rollup.config import Config
from rollup.filter import group_dated_by_folder
from rollup.models import DigestReport, DigestStats
from rollup.pipeline.session import _DigestSession, _failure_result
from rollup.pipeline.status import derive_run_status, status_to_exit_code
from rollup.pipeline.types import AggregatedResults, DigestRunResult, RenderResult
from rollup.render import digest_output_stem, render_html, render_markdown

logger = logging.getLogger(__name__)


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

    # Write the new batch first; archive the previous one after artifacts land
    # so a crash mid-write cannot leave the output root empty.

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
                allow_network=session.effective_run.allow_final_review_network,
                dry_run=run_options.dry_run,
                quiet=run_options.quiet,
                run_id_short=ctx.run_id_short,
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
            allow_network=session.effective_run.allow_final_review_network,
            dry_run=run_options.dry_run,
            quiet=run_options.quiet,
            run_id_short=ctx.run_id_short,
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
            batch = run_enabled_writers(
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
                    config=config,
                ),
                args=session.writer_cli_args,
                config=config,
            )
            aggregated.writer_artifacts.extend(batch.written)
            if batch.optional_failures:
                aggregated.optional_writer_failed = True
                for writer_name, writer_err in batch.optional_failures:
                    logger.warning("Optional output writer failed: %s", writer_err)
                    ctx.add_event(
                        "optional_writer_failed",
                        writer_err,
                        level="warning",
                        writer=writer_name,
                    )
        except OutputWriterError as writer_exc:
            aggregated.required_writer_failed = True
            aggregated.hard_failure = True
            aggregated.hard_failure_reason = str(writer_exc)
            session.error_message = str(writer_exc)
            logger.error("Required output writer failed: %s", writer_exc)
            ctx.add_event("required_writer_failed", str(writer_exc), level="error")
            session.status = "failure"
            _archive_keeping_current(session)
            if session.manifest_builder is not None:
                try:
                    session.manifest_builder.record_failure(writer_exc)
                    session.manifest_builder.finalize(
                        status="failure", aggregated=aggregated
                    )
                except Exception:
                    pass
            return _failure_result(session)

    _archive_keeping_current(session)

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


def _archive_keeping_current(session: _DigestSession) -> None:
    """Move prior dated artifacts out of the output root; keep this run's files."""
    from rollup.output_archive import archive_previous_outputs
    from rollup.render import digest_output_stem

    keep: set[str] = set()
    stems: set[str] = {
        digest_output_stem(
            session.generated_at, run_id_short=session.ctx.run_id_short
        ),
    }
    for render in session.aggregated.renders:
        if render.output_stem:
            stems.add(render.output_stem)
        if render.md_path is not None:
            keep.add(render.md_path.name)
        if render.html_path is not None:
            keep.add(render.html_path.name)
    if session.md_path is not None:
        keep.add(session.md_path.name)
    if session.html_path is not None:
        keep.add(session.html_path.name)
    for path in session.aggregated.writer_artifacts:
        keep.add(path.name)

    output_dir = session.config.output_dir
    if output_dir.is_dir():
        for candidate in output_dir.iterdir():
            if any(candidate.name.startswith(stem) for stem in stems):
                keep.add(candidate.name)

    archive_previous_outputs(
        output_dir,
        db_path=session.config.db_path,
        keep_names=keep,
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
    from rollup import pipeline as pipeline_mod

    return pipeline_mod.atomic_write_digest(
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
    allow_network: bool | None = None,
    dry_run: bool = False,
    quiet: bool = True,
    run_id_short: str | None = None,
) -> DigestReport:
    allowed = (
        allow_network
        if allow_network is not None
        else (config.final_review_enabled and not dry_run)
    )
    if not allowed:
        return report
    from rollup.final_review import (
        execute_final_review,
        print_final_review_summary,
        write_final_review_report,
    )

    stem = digest_output_stem(
        generated_at, variant_name, run_id_short=run_id_short
    )

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


