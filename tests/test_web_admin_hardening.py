"""Tests for read-only GET connections and Admin hardening."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("flask")

from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.state import (
    SchemaCompatibilityError,
    assert_schema_readable,
    connect_db_readonly,
    init_db,
)
from rollup.utc import format_utc
from rollup.web.app import create_app
from rollup.web.maintenance_tokens import (
    consume_maintenance_token,
    issue_maintenance_token,
)


def _seed_app(tmp_path: Path):
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
        messages_included=0,
        messages_skipped_outside_window=0,
        messages_skipped_seen_undated=0,
        messages_deduped=0,
        messages_skipped_disabled_source=0,
        groups_created=0,
        sources_included=0,
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
        entries=[],
        expected_entry_count=0,
    )
    index_rollup_run(db, payload)
    app = create_app(state_dir=state, output_dir=out, testing=True)
    return app, state, out, db


def test_connect_db_readonly_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        connect_db_readonly(tmp_path / "missing.db")


def test_connect_db_readonly_blocks_writes(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    init_db(db).close()
    conn = connect_db_readonly(db)
    assert assert_schema_readable(conn) >= 8
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE should_fail(x INTEGER)")
    conn.close()


def test_all_get_routes_no_store_and_readonly(tmp_path: Path) -> None:
    app, state, out, db = _seed_app(tmp_path)
    client = app.test_client()
    paths = ["/", "/rollups", "/sources", "/sources/registry", "/admin"]
    for path in paths:
        resp = client.get(path, follow_redirects=True)
        assert resp.status_code == 200, path
        assert "no-store" in (resp.headers.get("Cache-Control") or ""), path


def test_admin_get_does_not_call_run_doctor(tmp_path: Path) -> None:
    app, *_ = _seed_app(tmp_path)
    client = app.test_client()
    with mock.patch("rollup.doctor.run_doctor") as spy:
        resp = client.get("/admin")
        assert resp.status_code == 200
        spy.assert_not_called()
    body = resp.get_data(as_text=True)
    assert "incomplete" in body.lower() or "manifest" in body.lower()
    assert "deep check" in body.lower()


def test_deep_check_is_post_only(tmp_path: Path) -> None:
    app, *_ = _seed_app(tmp_path)
    client = app.test_client()
    assert client.get("/admin/deep-check").status_code in {405, 404}
    # GET ?deep=1 must not trigger deep mode
    resp = client.get("/admin?deep=1")
    assert resp.status_code == 200
    assert "Showing deep diagnostics" not in resp.get_data(as_text=True)


def test_deep_check_post_csrf(tmp_path: Path) -> None:
    app, *_ = _seed_app(tmp_path)
    client = app.test_client()
    # Without CSRF
    bad = client.post("/admin/deep-check", data={})
    assert bad.status_code == 400
    # With CSRF from session
    with client:
        client.get("/admin")
        from flask import session
        # Obtain token via form page then post
        page = client.get("/admin")
        html = page.get_data(as_text=True)
        # Extract csrf from hidden input roughly
        assert "csrf_token" in html
        # Use session token directly
        with app.test_request_context("/admin"):
            pass
    with client.session_transaction() as sess:
        from rollup.web.csrf import CSRF_SESSION_KEY, generate_csrf_token

        token = generate_csrf_token()
        sess[CSRF_SESSION_KEY] = token
    ok = client.post("/admin/deep-check", data={"csrf_token": token})
    assert ok.status_code == 200
    assert "Showing deep diagnostics" in ok.get_data(as_text=True)


def test_host_rejection_when_enforced(tmp_path: Path) -> None:
    app, *_ = _seed_app(tmp_path)
    app.config["WEB_ENFORCE_HOST"] = True
    app.config["WEB_BIND_HOST"] = "127.0.0.1"
    app.config["WEB_BIND_PORT"] = 8765
    client = app.test_client()
    resp = client.get(
        "/admin",
        headers={"Host": "evil.example", "X-Forwarded-Host": "127.0.0.1:8765"},
    )
    assert resp.status_code == 400


def test_manifest_scan_isolates_malformed(tmp_path: Path) -> None:
    app, state, out, db = _seed_app(tmp_path)
    mdir = state / "manifests"
    mdir.mkdir()
    (mdir / "bad.json").write_text("{not json", encoding="utf-8")
    good = {
        "schema_version": 2,
        "run_id": str(uuid.uuid4()),
        "started_at": "2024-06-02T00:00:00+00:00",
        "completed_at": "2024-06-02T00:01:00+00:00",
        "status": "failure",
        "mode": "cron",
        "rollup_version": "0.6.0",
        "counts": {"messages_seen": 0, "messages_parsed": 0, "messages_included": 0},
        "dated_outputs_written": False,
        "latest_outputs_updated": False,
        "errors": [{"code": "no_input"}],
    }
    (mdir / "good.json").write_text(json.dumps(good), encoding="utf-8")
    client = app.test_client()
    resp = client.get("/admin")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "manifest_malformed" in body or "Isolated malformed" in body
    assert "failure" in body


def test_maintenance_token_one_time(tmp_path: Path) -> None:
    from rollup.web.maintenance_tokens import clear_nonce_store_for_tests

    clear_nonce_store_for_tests()
    secret = "test-secret"
    tok = issue_maintenance_token(
        secret=secret,
        action="prune",
        scope_fingerprint="all",
        preview_fingerprint="abc",
    )
    ok, code = consume_maintenance_token(
        tok,
        secret=secret,
        action="prune",
        scope_fingerprint="all",
        preview_fingerprint="abc",
    )
    assert ok and code == "ok"
    ok2, code2 = consume_maintenance_token(
        tok,
        secret=secret,
        action="prune",
        scope_fingerprint="all",
        preview_fingerprint="abc",
    )
    assert not ok2 and code2 == "replay"


def test_deep_check_does_not_open_mutator(tmp_path: Path) -> None:
    app, *_ = _seed_app(tmp_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        from rollup.web.csrf import CSRF_SESSION_KEY, generate_csrf_token

        token = generate_csrf_token()
        sess[CSRF_SESSION_KEY] = token
    with mock.patch("rollup.web.db.open_mutator") as mut:
        ok = client.post("/admin/deep-check", data={"csrf_token": token})
        assert ok.status_code == 200
        mut.assert_not_called()


def test_admin_shows_digest_and_body_actions(tmp_path: Path) -> None:
    app, *_ = _seed_app(tmp_path)
    client = app.test_client()
    resp = client.get("/admin")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Default digest effective configuration" in body
    assert "Preview backfill" in body
    assert "Preview delete all" in body
    assert "Preview vacuum" in body
