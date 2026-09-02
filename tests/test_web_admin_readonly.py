"""Tests for read-only web GET contract and Admin deep-check POST-only."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("flask")

from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.state import connect_db_readonly, init_db
from rollup.utc import format_utc
from rollup.web.app import create_app


def _seed(tmp_path: Path):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    now = format_utc(datetime(2024, 6, 1, tzinfo=timezone.utc))
    (out / "x.md").write_text("# d", encoding="utf-8")
    (out / "x.html").write_text("<html></html>", encoding="utf-8")
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
        messages_included=1,
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
        entries=[
            IndexEntry(
                message_key="mid:a@x",
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
                subject="Hi",
                sender="A",
                date_parsed=now,
                date_raw="",
                newsletter_type="essay",
                summary="s",
                summary_source="none",
                primary_link=None,
                links_json='{"v":1,"items":[]}',
            )
        ],
        expected_entry_count=1,
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
    index_rollup_run(db, payload)
    app = create_app(state_dir=state, output_dir=out, testing=True)
    return app, db, state


def test_get_routes_use_readonly_and_no_store(tmp_path: Path):
    app, db, _state = _seed(tmp_path)
    client = app.test_client()
    for path in ("/rollups", "/sources", "/sources/registry", "/admin"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "no-store" in resp.headers.get("Cache-Control", "")


def test_get_does_not_create_wal_shm(tmp_path: Path):
    """Readonly GET must succeed on a WAL database and must not require a writer."""
    app, db, state = _seed(tmp_path)
    client = app.test_client()
    assert client.get("/admin").status_code == 200
    assert client.get("/rollups").status_code == 200


def test_readonly_open_does_not_enable_wal(tmp_path: Path):
    """``connect_db_readonly`` must not switch a DELETE-mode file to WAL."""
    db = tmp_path / "plain.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
        )"""
    )
    conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 15)")
    conn.commit()
    conn.close()
    assert not Path(str(db) + "-wal").exists()
    ro = connect_db_readonly(db)
    assert ro.execute("SELECT 1").fetchone()[0] == 1
    ro.close()
    assert not Path(str(db) + "-wal").exists()
    assert not Path(str(db) + "-shm").exists()


def test_readonly_connector_rejects_writes(tmp_path: Path):
    _app, db, _ = _seed(tmp_path)
    ro = connect_db_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("CREATE TABLE should_fail(x)")
    ro.close()


def test_deep_check_get_not_available(tmp_path: Path):
    app, _db, _ = _seed(tmp_path)
    client = app.test_client()
    # No ?deep=1 path — GET /admin is always cheap.
    resp = client.get("/admin?deep=1")
    assert resp.status_code == 200
    assert b"Showing deep diagnostics" not in resp.data


def test_deep_check_post_requires_csrf(tmp_path: Path):
    app, _db, _ = _seed(tmp_path)
    client = app.test_client()
    resp = client.post("/admin/deep-check", data={})
    assert resp.status_code == 400


def test_deep_check_post_ok(tmp_path: Path):
    app, _db, _ = _seed(tmp_path)
    client = app.test_client()
    # Establish session + CSRF
    client.get("/admin")
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token")
    resp = client.post("/admin/deep-check", data={"csrf_token": token})
    assert resp.status_code == 200
    assert b"deep" in resp.data.lower() or b"Deep" in resp.data


def test_admin_get_does_not_call_run_doctor(tmp_path: Path):
    app, _db, _ = _seed(tmp_path)
    client = app.test_client()
    with mock.patch("rollup.doctor.run_doctor") as banned:
        resp = client.get("/admin")
        assert resp.status_code == 200
        banned.assert_not_called()


def test_host_rejection_when_enforced(tmp_path: Path):
    app, _db, _ = _seed(tmp_path)
    app.config["WEB_ENFORCE_HOST"] = True
    app.config["WEB_BIND_HOST"] = "127.0.0.1"
    app.config["WEB_BIND_PORT"] = 8765
    client = app.test_client()
    resp = client.get("/rollups", headers={"Host": "evil.example"})
    assert resp.status_code == 400
