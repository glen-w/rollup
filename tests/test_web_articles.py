"""Web articles queue route tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.state import init_db
from rollup.web.app import create_app
from rollup.web.csrf import CSRF_SESSION_KEY
from rollup.web.run_runner import clear_finished_for_tests
from rollup.webpage.queue import count_pending, enqueue_url, list_by_status


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clear_finished_for_tests()
    state = tmp_path / "state"
    out = tmp_path / "out"
    mail = tmp_path / "mail"
    root = mail / "Newsletters.sbd"
    state.mkdir()
    out.mkdir()
    root.mkdir(parents=True)
    init_db(state / "rollup.db").close()
    cfg = tmp_path / "rollup.toml"
    cfg.write_text(
        "\n".join(
            [
                f'root = "{root}"',
                f'mail_root = "{mail}"',
                f'output_dir = "{out}"',
                f'state_dir = "{state}"',
                'lookback_days = 7',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    application = create_app(
        state_dir=state,
        output_dir=out,
        mail_root=mail,
        newsletter_root=root,
        testing=True,
        config_path=cfg,
    )
    return application


def _csrf(client, token: str = "test-csrf-token") -> str:
    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = token
    return token


def test_articles_index_empty(app) -> None:
    client = app.test_client()
    resp = client.get("/articles")
    assert resp.status_code == 200
    assert b"No pending articles" in resp.data


def test_articles_add_and_remove(app) -> None:
    client = app.test_client()
    token = _csrf(client)
    resp = client.post(
        "/articles/add",
        data={
            "csrf_token": token,
            "url": "https://example.com/my-article",
            "display_title": "My article",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"My article" in resp.data
    conn = init_db(Path(app.config["STATE_DIR"]) / "rollup.db")
    assert count_pending(conn) == 1
    row = conn.execute("SELECT id FROM webpage_queue").fetchone()
    conn.close()

    token = _csrf(client)
    resp = client.post(
        f"/articles/{row[0]}/remove",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    conn = init_db(Path(app.config["STATE_DIR"]) / "rollup.db")
    assert count_pending(conn) == 0
    conn.close()


def test_articles_retry_failed(app) -> None:
    client = app.test_client()
    conn = init_db(Path(app.config["STATE_DIR"]) / "rollup.db")
    item = enqueue_url(conn, "https://example.com/failed-article")
    conn.execute(
        "UPDATE webpage_queue SET status = 'failed', error_code = 'webpage_empty' WHERE id = ?",
        (item.id,),
    )
    conn.commit()
    conn.close()

    token = _csrf(client)
    resp = client.post(
        f"/articles/{item.id}/retry",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    conn = init_db(Path(app.config["STATE_DIR"]) / "rollup.db")
    pending = list_by_status(conn, "pending", limit=10)
    conn.close()
    assert any(p.id == item.id for p in pending)
