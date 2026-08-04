"""Offline tests for web navigation helpers and admin route."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.interaction import dismiss
from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.state import init_db
from rollup.utc import format_utc
from rollup.web.app import create_app
from rollup.web.navigation import build_reader_nav, parse_run_query
from rollup.web_ids import encode_opaque, encode_run_opaque


def _app_with_entries(tmp_path: Path, *, n: int = 3):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    now = format_utc(datetime(2024, 6, 1, tzinfo=timezone.utc))
    (out / "x.md").write_text("# digest", encoding="utf-8")
    (out / "x.html").write_text("<html></html>", encoding="utf-8")
    entries = []
    for i in range(n):
        mk = f"mid:msg{i}@x"
        entries.append(
            IndexEntry(
                message_key=mk,
                source_key_observed="from:a@example.com",
                group_id=None,
                group_type=None,
                group_display_name=None,
                section_key="tech",
                section_position=0,
                group_position=None,
                entry_position=i,
                display_position=i,
                folder_name="tech",
                subject=f"Hello {i}",
                sender="A",
                date_parsed=now,
                date_raw="",
                newsletter_type="essay",
                summary="Plain summary",
                summary_source="none",
                primary_link=None,
                links_json='{"v":1,"items":[]}',
            )
        )
    conn = init_db(db)
    conn.execute(
        """INSERT INTO sources (source_key, identity_version, lifecycle,
           display_name_observed, created_at, updated_at)
           VALUES ('from:a@example.com', 1, 'active', 'A', ?, ?)""",
        (now, now),
    )
    conn.commit()
    conn.close()
    payload = RunIndexPayload(
        run_id=run_id,
        started_at=now,
        completed_at=now,
        status="success",
        mode="manual",
        rollup_version="0.5.0",
        manifest_schema_version=2,
        report_schema_version=1,
        stats_completeness="full",
        window_start=now,
        window_end=now,
        lookback_days=7,
        digest_fingerprint="abc",
        messages_included=n,
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
        markdown_relpath="x.md",
        html_relpath="x.html",
        index_source="pipeline",
        entries=entries,
        expected_entry_count=n,
    )
    index_rollup_run(db, payload)
    app = create_app(
        state_dir=state,
        output_dir=out,
        mail_root=tmp_path / "mail",
        testing=True,
    )
    return app, db, run_id, [e.message_key for e in entries]


def test_parse_run_query_roundtrip() -> None:
    run_id = str(uuid.uuid4())
    token = encode_run_opaque(run_id)
    assert parse_run_query(token) == run_id
    assert parse_run_query(None) is None
    assert parse_run_query("not-a-token") is None


def test_build_reader_nav_prev_next(tmp_path: Path) -> None:
    app, db, run_id, keys = _app_with_entries(tmp_path, n=3)
    conn = init_db(db)
    with app.test_request_context("/"):
        mid = build_reader_nav(conn, run_id=run_id, message_key=keys[1])
        assert mid is not None
        assert mid["prev"] is not None
        assert mid["next"] is not None
        assert encode_opaque(keys[0]) in mid["prev"]
        assert encode_opaque(keys[2]) in mid["next"]

        first = build_reader_nav(conn, run_id=run_id, message_key=keys[0])
        assert first is not None
        assert first["prev"] is None
        assert first["next"] is not None

        last = build_reader_nav(conn, run_id=run_id, message_key=keys[2])
        assert last is not None
        assert last["next"] is None

        assert build_reader_nav(conn, run_id=run_id, message_key="mid:missing@x") is None
    conn.close()


def test_build_reader_nav_hides_dismissed(tmp_path: Path) -> None:
    app, db, run_id, keys = _app_with_entries(tmp_path, n=3)
    conn = init_db(db)
    dismiss(conn, keys[1], now=datetime(2024, 6, 2, tzinfo=timezone.utc))
    conn.commit()
    with app.test_request_context("/"):
        nav = build_reader_nav(conn, run_id=run_id, message_key=keys[0])
        assert nav is not None
        assert encode_opaque(keys[2]) in (nav["next"] or "")
        hidden = build_reader_nav(conn, run_id=run_id, message_key=keys[1])
        assert hidden is None
        shown = build_reader_nav(
            conn, run_id=run_id, message_key=keys[1], show_dismissed=True
        )
        assert shown is not None
    conn.close()


def test_admin_index_ok(tmp_path: Path) -> None:
    app, _db, _run_id, _keys = _app_with_entries(tmp_path, n=1)
    client = app.test_client()
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "no-store" in (resp.headers.get("Cache-Control") or "")
    body = resp.get_data(as_text=True)
    assert "Admin" in body or "admin" in body.lower()
    assert "rollup.db" in body or "Database" in body
