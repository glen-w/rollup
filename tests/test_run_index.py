"""Schema v8 and run indexing tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.models import (
    ClassifiedMessage,
    DigestEntry,
    DigestReport,
    DigestStats,
    ParsedMessage,
)
from rollup.payload_limits import ENTRY_INDEX_VERSION
from rollup.run_index import (
    RunIndexError,
    build_pipeline_payload,
    index_rollup_run,
)
from rollup.state import SCHEMA_VERSION, get_schema_version, init_db


def _parsed(key: str, source: str | None = "from:a@example.com") -> ParsedMessage:
    return ParsedMessage(
        message_key=key,
        content_hash="c" * 64,
        folder_name="tech",
        relative_folder_path="tech",
        subject="Hello",
        sender="A <a@example.com>",
        date_raw="Mon, 1 Jan 2024 12:00:00 +0000",
        date_parsed=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        body_text="body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="body",
        parse_warnings=(),
        source_key=source,
        list_id=None,
    )


def _report(entries: list[DigestEntry]) -> DigestReport:
    return DigestReport(
        generated_at=datetime(2024, 1, 8, tzinfo=timezone.utc),
        lookback_days=7,
        window_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2024, 1, 8, tzinfo=timezone.utc),
        dated_by_folder={"tech": tuple(entries)},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=len(entries),
            dated_included=len(entries),
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=0,
            summaries_errors=0,
        ),
    )


def test_schema_v8_tables(tmp_path: Path):
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION == 8
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "rollup_runs" in tables
    assert "message_ratings" in tables
    assert "message_interaction" in tables
    # updated_by allows web
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='source_overrides'"
    ).fetchone()[0]
    assert "web" in sql
    conn.close()


def test_index_rollup_run_transactional(tmp_path: Path):
    db = tmp_path / "rollup.db"
    out = tmp_path / "out"
    out.mkdir()
    md = out / "d.md"
    html = out / "d.html"
    md.write_text("md", encoding="utf-8")
    html.write_text("html", encoding="utf-8")
    run_id = str(uuid.uuid4())
    entry = DigestEntry(
        classified=ClassifiedMessage(
            parsed=_parsed("mid:one@x"), newsletter_type="essay", classification_scores=()
        ),
        summary="sum",
        summary_source="none",
    )
    report = _report([entry])
    payload = build_pipeline_payload(
        run_id=run_id,
        report=report,
        status="success",
        mode="manual",
        rollup_version="0.5.0",
        started_at=datetime(2024, 1, 8, tzinfo=timezone.utc),
        completed_at=datetime(2024, 1, 8, 1, tzinfo=timezone.utc),
        md_path=md,
        html_path=html,
        manifest_path=None,
        output_dir=out,
        state_dir=tmp_path,
    )
    index_rollup_run(db, payload)
    conn = init_db(db)
    row = conn.execute(
        "SELECT entry_index_version, messages_included FROM rollup_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    assert row[0] == ENTRY_INDEX_VERSION
    assert row[1] == 1
    assert conn.execute("SELECT COUNT(*) FROM rollup_entries").fetchone()[0] == 1
    # Re-index with same run_id should replace entries, not cascade-wipe mid-flight
    entry2 = DigestEntry(
        classified=ClassifiedMessage(
            parsed=_parsed("mid:two@x"), newsletter_type="essay", classification_scores=()
        ),
        summary="sum2",
        summary_source="none",
    )
    payload2 = build_pipeline_payload(
        run_id=run_id,
        report=_report([entry2]),
        status="success",
        mode="manual",
        rollup_version="0.5.0",
        started_at=datetime(2024, 1, 8, tzinfo=timezone.utc),
        completed_at=datetime(2024, 1, 8, 1, tzinfo=timezone.utc),
        md_path=md,
        html_path=html,
        manifest_path=None,
        output_dir=out,
        state_dir=tmp_path,
    )
    index_rollup_run(db, payload2)
    keys = [
        r[0]
        for r in conn.execute(
            "SELECT message_key FROM rollup_entries WHERE run_id=?", (run_id,)
        ).fetchall()
    ]
    assert keys == ["mid:two@x"]
    conn.close()


def test_index_rejects_dry_run_status(tmp_path: Path):
    from rollup.run_index import RunIndexPayload

    with pytest.raises(RunIndexError):
        index_rollup_run(
            tmp_path / "db.sqlite",
            RunIndexPayload(
                run_id=str(uuid.uuid4()),
                started_at="2024-01-01T00:00:00Z",
                completed_at=None,
                status="dry_run",  # type: ignore[arg-type]
                mode=None,
                rollup_version=None,
                manifest_schema_version=None,
                report_schema_version=None,
                stats_completeness="full",
                window_start=None,
                window_end=None,
                lookback_days=None,
                digest_fingerprint=None,
                messages_included=0,
                messages_skipped_outside_window=None,
                messages_skipped_seen_undated=None,
                messages_deduped=None,
                messages_skipped_disabled_source=None,
                groups_created=None,
                sources_included=None,
                summaries_ollama=None,
                summaries_cache=None,
                summaries_fallback=None,
                summaries_errors=None,
                summaries_final_review_applied=None,
                group_summaries_succeeded=None,
                warning_count=None,
                degraded=False,
                manifest_relpath=None,
                markdown_relpath=None,
                html_relpath=None,
                index_source="pipeline",
                entries=[],
            ),
        )
