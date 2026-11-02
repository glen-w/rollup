"""GET /search is read-only; POST /ask requires CSRF."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.corpus import fts5_available, upsert_corpus_row
from rollup.state import init_db
from rollup.utc import format_utc
from rollup.web.app import create_app

pytestmark = pytest.mark.skipif(
    not fts5_available(),
    reason="SQLite FTS5 is not available in this build",
)


def _app(tmp_path: Path):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    conn = init_db(db)
    upsert_corpus_row(
        conn,
        message_key="mid:search@x",
        title="Helium balloon briefing",
        body_text="Helium notes",
        folder="tech",
    )
    now = format_utc(datetime(2026, 7, 2, tzinfo=timezone.utc))
    conn.execute(
        "UPDATE message_reader_bodies SET updated_at = ? WHERE 1=0",
        (now,),
    )
    conn.commit()
    conn.close()
    app = create_app(state_dir=state, output_dir=out, testing=True)
    return app, db


def test_get_search_empty_form(tmp_path: Path) -> None:
    app, _db = _app(tmp_path)
    client = app.test_client()
    resp = client.get("/search")
    assert resp.status_code == 200
    assert b"Search" in resp.data
    assert b"Ask Rollup is off" in resp.data


def test_get_search_hits_and_does_not_write(tmp_path: Path) -> None:
    app, db = _app(tmp_path)
    before = db.read_bytes()
    client = app.test_client()
    resp = client.get("/search?q=helium")
    assert resp.status_code == 200
    assert b"Helium balloon briefing" in resp.data
    assert "no-store" in resp.headers.get("Cache-Control", "")
    after = db.read_bytes()
    assert after == before


def test_post_ask_requires_csrf(tmp_path: Path) -> None:
    app, _db = _app(tmp_path)
    client = app.test_client()
    resp = client.post("/ask", data={"q": "helium"})
    assert resp.status_code == 400


def test_post_ask_empty_hits_refuses(tmp_path: Path) -> None:
    app, _db = _app(tmp_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    resp = client.post(
        "/ask", data={"q": "zzzz-no-hits", "csrf_token": "test-csrf-token"}
    )
    assert resp.status_code == 200
    assert b"No matching items in the Rollup cache." in resp.data
