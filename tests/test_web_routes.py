"""Web route and CSRF tests (requires flask)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.ratings import set_rating_with_reasons
from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.state import init_db
from rollup.utc import format_utc
from rollup.web.app import create_app
from rollup.web_ids import encode_opaque


@pytest.fixture
def app_env(tmp_path: Path):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    now = format_utc(datetime(2024, 6, 1, tzinfo=timezone.utc))
    md = out / "x.md"
    html = out / "x.html"
    md.write_text("# digest", encoding="utf-8")
    html.write_text("<html></html>", encoding="utf-8")
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
                message_key="mid:msg@x",
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
                subject="Hello <script>",
                sender="A",
                date_parsed=now,
                date_raw="",
                newsletter_type="essay",
                summary="Plain summary",
                summary_source="none",
                primary_link="https://example.com/",
                links_json='{"v":1,"items":[{"href":"https://example.com/","label":"Ex"}]}',
            )
        ],
        expected_entry_count=1,
    )
    # need source anchor for policy tests
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
    return app, run_id


def test_archive_and_detail(app_env):
    app, run_id = app_env
    client = app.test_client()
    r = client.get("/rollups")
    assert r.status_code == 200
    assert run_id.encode() in r.data or b"Hello" in r.data or b"web" in r.data
    d = client.get(f"/rollups/{run_id}")
    assert d.status_code == 200
    assert b"Plain summary" in d.data
    assert b"<script>" not in d.data or b"&lt;script&gt;" in d.data


def test_csrf_rejection(app_env):
    app, _run_id = app_env
    client = app.test_client()
    enc = encode_opaque("mid:msg@x")
    r = client.post(
        f"/messages/{enc}/rating",
        data={"stars": "5", "csrf_token": "bad"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_rating_post(app_env):
    app, run_id = app_env
    client = app.test_client()
    # get csrf from page
    page = client.get(f"/rollups/{run_id}")
    assert page.status_code == 200
    # extract token from html
    import re

    m = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
    assert m
    token = m.group(1).decode()
    enc = encode_opaque("mid:msg@x")
    r = client.post(
        f"/messages/{enc}/rating",
        data={"stars": "5", "csrf_token": token, "next": f"/rollups/{run_id}"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    with app.app_context():
        from flask import g
        # open via client context
    conn = init_db(Path(app.config["DB_PATH"]))
    from rollup.ratings import get_rating

    rating = get_rating(conn, "mid:msg@x")
    assert rating is not None and rating.stars == 5
    conn.close()


def test_artifact_serve(app_env):
    app, run_id = app_env
    client = app.test_client()
    r = client.get(f"/artifacts/{run_id}/md")
    assert r.status_code == 200
    assert b"# digest" in r.data


def test_bind_rejects_wildcard():
    from rollup.web.bind import BindError, validate_bind_host

    with pytest.raises(BindError):
        validate_bind_host("0.0.0.0")
    with pytest.raises(BindError):
        validate_bind_host("::")
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    assert validate_bind_host("::1") == "::1"
