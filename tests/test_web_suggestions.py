"""Quality/Registry suggestion cards and apply via set_overrides."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.models import ParsedMessage
from rollup.parse import compute_content_hash
from rollup.source_registry import get_source_record, observe_sources
from rollup.state import init_db
from rollup.web.app import create_app
from rollup.web_ids import encode_opaque


def _msg() -> ParsedMessage:
    body = "old"
    return ParsedMessage(
        message_key="mid:old@x",
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject="old",
        sender="A <old@ex.com>",
        date_raw="",
        date_parsed=datetime(2026, 8, 1, tzinfo=timezone.utc),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview=body,
        parse_warnings=(),
        source_key="from:old@ex.com",
    )


def _app(tmp_path: Path):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    conn = init_db(db)
    observe_sources(
        conn,
        [_msg()],
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    conn.close()
    app = create_app(state_dir=state, output_dir=out, testing=True)
    return app, db


def test_quality_and_registry_show_suggestions(tmp_path: Path) -> None:
    app, _db = _app(tmp_path)
    client = app.test_client()
    quality = client.get("/sources")
    assert quality.status_code == 200
    assert b"unused_8w" in quality.data
    assert b"No mail in 56 days" in quality.data
    registry = client.get("/sources/registry")
    assert registry.status_code == 200
    assert b"unused_8w" in registry.data
    assert b"No mail in 56 days" in registry.data


def test_suggest_apply_disable_uses_overrides(tmp_path: Path) -> None:
    app, db = _app(tmp_path)
    client = app.test_client()
    enc = encode_opaque("from:old@ex.com")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tok"
    resp = client.post(
        f"/sources/{enc}/suggest-apply",
        data={"csrf_token": "tok", "action": "disable", "next": "/sources"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    conn = init_db(db)
    rec = get_source_record(conn, "from:old@ex.com")
    assert rec.policy.enabled is False
    conn.close()


def test_suggest_apply_requires_csrf(tmp_path: Path) -> None:
    app, _db = _app(tmp_path)
    client = app.test_client()
    enc = encode_opaque("from:old@ex.com")
    resp = client.post(
        f"/sources/{enc}/suggest-apply",
        data={"action": "disable"},
    )
    assert resp.status_code == 400
