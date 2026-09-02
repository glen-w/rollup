"""Web Reddit configuration route tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.state import init_db
from rollup.user_config import load_toml_file
from rollup.web.app import create_app
from rollup.web.csrf import CSRF_SESSION_KEY
from rollup.web.run_runner import clear_finished_for_tests


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
    return application, cfg


def _csrf(client, token: str = "test-csrf-token") -> str:
    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = token
    return token


def test_reddit_index_empty(app) -> None:
    application, _cfg = app
    client = application.test_client()
    resp = client.get("/reddit")
    assert resp.status_code == 200
    assert b"Add subreddit" in resp.data
    assert b"public RSS" in resp.data
    assert b"Refresh subscriptions" not in resp.data
    assert b"ROLLUP_REDDIT" not in resp.data
    assert b"70s between subs" in resp.data


def test_reddit_save_adds_sub(app) -> None:
    application, cfg = app
    client = application.test_client()
    token = _csrf(client)
    resp = client.post(
        "/reddit/save",
        data={
            "csrf_token": token,
            "reddit_enabled": "1",
            "reddit_layout": "feed",
            "reddit_sort": "hot",
            "reddit_mode": "summary",
            "reddit_limit": "10",
            "add_sub": "python",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    loaded = load_toml_file(cfg)
    assert loaded.reddit.enabled is True
    assert "python" in loaded.reddit.subs
    assert loaded.reddit.subs["python"].enabled is True


def test_reddit_index_shows_fetch_estimate(app) -> None:
    application, cfg = app
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + "\n".join(
            [
                "",
                "[reddit]",
                "enabled = true",
                "[reddit.subs.python]",
                "enabled = true",
                "[reddit.subs.rust]",
                "enabled = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    client = application.test_client()
    resp = client.get("/reddit")
    assert resp.status_code == 200
    assert b"about 1 min" in resp.data
    assert b"2 enabled subs" in resp.data


def test_reddit_save_requires_csrf(app) -> None:
    application, _cfg = app
    client = application.test_client()
    resp = client.post(
        "/reddit/save",
        data={"reddit_enabled": "1", "add_sub": "python"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
