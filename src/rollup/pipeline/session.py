"""Mutable digest-run session and lock / path / resource helpers."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rollup.config import Config
from rollup.models import DigestReport, DigestStats
from rollup.pipeline.status import EXIT_FAILURE, derive_run_status
from rollup.pipeline.types import AggregatedResults, DigestRunResult, SummarizeResult
from rollup.run_context import RunContext, RunStatus
from rollup.run_options import GroupingConfig, ManifestConfig, RunOptions

if TYPE_CHECKING:
    from rollup.output_writers import OutputWriter

logger = logging.getLogger(__name__)


@dataclass
class _DigestSession:
    """Mutable run state for ``run_digest`` phase helpers."""

    config: Config
    run_options: RunOptions
    grouping: GroupingConfig
    manifest_config: ManifestConfig
    acquire_lock: bool
    output_writers: Mapping[str, OutputWriter] | None
    writer_cli_args: argparse.Namespace | None
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
    attempt_started: bool = False
    manifest_builder: Any = None
    summarize_result: SummarizeResult | None = None
    continuity_unreadable: bool = False


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
            try:
                from rollup.attempts import start_attempt
                from rollup.state import init_db

                attempt_conn = init_db(config.db_path)
                try:
                    start_attempt(
                        attempt_conn,
                        run_id=session.ctx.run_id,
                        started_at=session.generated_at,
                    )
                    session.attempt_started = True
                finally:
                    attempt_conn.close()
            except Exception as exc:
                logger.warning("Could not record digest attempt: %s", exc)
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


def _release_resources(session: _DigestSession) -> None:
    if session.attempt_started:
        try:
            from datetime import datetime

            from rollup.attempts import finish_attempt
            from rollup.state import init_db

            attempt_conn = init_db(session.config.db_path)
            try:
                stored = session.status if session.status in {"success", "partial", "failure"} else "failure"
                finish_attempt(
                    attempt_conn,
                    run_id=session.ctx.run_id,
                    status=stored,
                    finished_at=datetime.now().astimezone(),
                    error_summary=session.error_message,
                )
            finally:
                attempt_conn.close()
        except Exception as exc:
            logger.warning("Could not finish digest attempt: %s", exc)
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


