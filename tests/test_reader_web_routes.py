"""Web reader body route tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.parse import compute_content_hash
from rollup.reader_bodies import make_reader_body_write
from rollup.reader_body_store import upsert_reader_bodies
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


def test_body_partial(app_env):
    app, id_enc = app_env
    client = app.test_client()
    r = client.get(f"/messages/{id_enc}/body?partial=1")
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("text/html")
    assert b"data-reader-body-fragment" in r.data


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
