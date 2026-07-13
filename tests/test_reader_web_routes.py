"""Web reader body route tests."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.parse import compute_content_hash
from rollup.reader_bodies import make_reader_body_write
from rollup.reader_body_store import upsert_reader_bodies
from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.state import init_db
from rollup.utc import format_utc, now_utc
from rollup.web_ids import encode_opaque


@pytest.fixture
def app_env(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    from rollup.web.app import create_app

    app = create_app(state_dir=state, output_dir=output, testing=True)
    db = init_db(state / "rollup.db")
    key = "mid:msg@example.com"
    ch = compute_content_hash("Hello **world** https://example.com")
    w = make_reader_body_write(key, ch, "Hello **world** https://example.com")
    upsert_reader_bodies(db, [w], seen_at=format_utc(now_utc()))
    db.commit()
    db.close()
    return app, encode_opaque(key)


def test_body_get_full(app_env):
    app, id_enc = app_env
    client = app.test_client()
    r = client.get(f"/messages/{id_enc}/body")
    assert r.status_code == 200
    assert b"data-reader-body-fragment" in r.data
    assert b"example.com" in r.data
    assert b"site-header" in r.data


def test_body_partial(app_env):
    app, id_enc = app_env
    client = app.test_client()
    r = client.get(f"/messages/{id_enc}/body?partial=1")
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("text/html")
    assert b"data-reader-body-fragment" in r.data
    assert b"site-header" not in r.data
    assert b"reader-page" not in r.data


def test_entry_card_partial_url_is_not_appended_to_run_query(tmp_path: Path):
    """Regression: body_url already has ?run=…; naive ?partial=1 broke partial fetch."""
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    run_id = str(uuid.uuid4())
    now = format_utc(datetime(2024, 6, 1, tzinfo=timezone.utc))
    (out / "x.md").write_text("# digest", encoding="utf-8")
    (out / "x.html").write_text("<html></html>", encoding="utf-8")
    key = "mid:reader@example.com"
    conn = init_db(db)
    conn.execute(
        """INSERT INTO sources (source_key, identity_version, lifecycle,
           display_name_observed, created_at, updated_at)
           VALUES ('from:a@example.com', 1, 'active', 'A', ?, ?)""",
        (now, now),
    )
    conn.commit()
    body = "Newsletter plaintext body"
    upsert_reader_bodies(
        conn,
        [make_reader_body_write(key, compute_content_hash(body), body)],
        seen_at=now,
    )
    conn.commit()
    conn.close()
    payload = RunIndexPayload(
        run_id=run_id,
        started_at=now,
        completed_at=now,
        status="success",
        mode="manual",
        rollup_version="0.5.1",
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
                message_key=key,
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
                subject="Reader card",
                sender="A",
                date_parsed=now,
                date_raw="",
                newsletter_type="essay",
                summary="Summary",
                summary_source="none",
                primary_link="https://example.com/",
                links_json='{"v":1,"items":[]}',
            )
        ],
        expected_entry_count=1,
    )
    index_rollup_run(db, payload)
    from rollup.web.app import create_app

    app = create_app(state_dir=state, output_dir=out, testing=True)
    client = app.test_client()
    page = client.get(f"/rollups/{run_id}")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "Read newsletter" in html
    m = re.search(r'data-body-url="([^"]+)"', html)
    assert m, "missing data-body-url on reader expander"
    body_url = m.group(1).replace("&amp;", "&")
    # Must be a real query param, not …?run=…?partial=1
    assert body_url.endswith("?partial=1")
    assert body_url.count("?") == 1
    assert "run=" not in body_url
    id_enc = encode_opaque(key)
    assert f"/messages/{id_enc}/body?partial=1" == body_url
    partial = client.get(body_url)
    assert partial.status_code == 200
    assert b"Newsletter plaintext body" in partial.data
    assert b"site-header" not in partial.data


def test_body_head(app_env):
    app, id_enc = app_env
    client = app.test_client()
    r = client.head(f"/messages/{id_enc}/body")
    assert r.status_code == 200
    assert r.data == b""


def test_body_missing(app_env):
    app, _id_enc = app_env
    client = app.test_client()
    missing = encode_opaque("mid:missing@example.com")
    r = client.get(f"/messages/{missing}/body")
    assert r.status_code == 404


def test_body_partial_escapes_raw_html(tmp_path: Path):
    """Stored plaintext that looks like HTML must be escaped, never executed."""
    state = tmp_path / "state"
    state.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    from rollup.web.app import create_app

    app = create_app(state_dir=state, output_dir=output, testing=True)
    db = init_db(state / "rollup.db")
    key = "mid:html-escape@example.com"
    body = '<img src=x onerror=alert(1)><script>evil()</script><b>ok</b>'
    w = make_reader_body_write(key, compute_content_hash(body), body)
    upsert_reader_bodies(db, [w], seen_at=format_utc(now_utc()))
    db.commit()
    db.close()
    id_enc = encode_opaque(key)
    client = app.test_client()
    r = client.get(f"/messages/{id_enc}/body?partial=1")
    assert r.status_code == 200
    data = r.data.decode("utf-8")
    assert 'data-reader-body-fragment' in data
    assert "<script>" not in data
    assert "<img " not in data
    assert "<b>" not in data
    assert "&lt;script&gt;" in data
    assert "&lt;b&gt;ok&lt;/b&gt;" in data
