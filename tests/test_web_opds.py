"""Loopback OPDS catalog and sibling artifact serving."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.state import init_db
from rollup.utc import format_utc
from rollup.web.app import create_app

NOW = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)


def test_opds_catalog_lists_indexed_briefing(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    conn = init_db(db)
    conn.close()
    run_id = str(uuid.uuid4())
    iso = format_utc(NOW)
    stem = "2026-07-02T103000Z-abcd1234-newsletter-digest"
    (out / f"{stem}.md").write_text("# digest", encoding="utf-8")
    (out / f"{stem}.html").write_text("<html>hi</html>", encoding="utf-8")
    (out / f"{stem}.epub").write_bytes(b"PK")
    payload = RunIndexPayload(
        run_id=run_id,
        started_at=iso,
        completed_at=iso,
        status="success",
        mode="manual",
        rollup_version="0.10.0",
        manifest_schema_version=2,
        report_schema_version=1,
        stats_completeness="full",
        window_start=iso,
        window_end=iso,
        lookback_days=7,
        digest_fingerprint="abc",
        messages_included=3,
        messages_skipped_outside_window=0,
        messages_skipped_seen_undated=0,
        messages_deduped=0,
        messages_skipped_disabled_source=0,
        groups_created=0,
        sources_included=1,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=0,
        summaries_errors=0,
        summaries_final_review_applied=0,
        group_summaries_succeeded=0,
        warning_count=0,
        degraded=False,
        manifest_relpath=None,
        markdown_relpath=f"{stem}.md",
        html_relpath=f"{stem}.html",
        index_source="pipeline",
        entries=[
            IndexEntry(
                message_key="mid:1",
                source_key_observed="from:a@example.com",
                group_id=None,
                group_type=None,
                group_display_name=None,
                section_key="tech",
                section_position=0,
                group_position=None,
                entry_position=0,
                display_position=0,
                folder_name="tech",
                subject="Hello",
                sender="A",
                date_parsed=iso,
                date_raw="",
                newsletter_type="essay",
                summary="Sum",
                summary_source="none",
                primary_link=None,
                links_json='{"v":1,"items":[]}',
            )
        ],
        expected_entry_count=1,
    )
    index_rollup_run(db, payload)
    app = create_app(state_dir=state, output_dir=out, testing=True)
    client = app.test_client()
    resp = client.get("/opds")
    assert resp.status_code == 200
    assert "application/atom+xml" in resp.content_type
    body = resp.get_data(as_text=True)
    assert "Rollup briefings" in body
    assert f"/artifacts/{run_id}/html" in body
    assert f"/artifacts/{run_id}/epub" in body

    epub = client.get(f"/artifacts/{run_id}/epub")
    assert epub.status_code == 200
    assert epub.data == b"PK"
