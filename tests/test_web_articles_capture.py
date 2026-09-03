"""Capture API, extension token, and Articles pairing tests."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.state import init_db
from rollup.web.app import create_app
from rollup.web.csrf import CSRF_SESSION_KEY
from rollup.web.run_runner import clear_finished_for_tests
from rollup.web.secrets import (
    WebSecretError,
    load_or_create_extension_token,
    rotate_extension_token,
)
from rollup.webpage.queue import count_pending, enqueue_url, get_by_id, mark_failed


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
                "lookback_days = 7",
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


def _auth(app) -> dict[str, str]:
    return {"Authorization": f"Bearer {app.config['EXTENSION_TOKEN']}"}


def test_articles_index_shows_pairing_token(app) -> None:
    client = app.test_client()
    resp = client.get("/articles")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Firefox extension" in body
    assert 'id="extension-token"' in body
    token = app.config["EXTENSION_TOKEN"]
    assert token
    assert token in body
    path = Path(app.config["STATE_DIR"]) / "extension_token"
    assert path.is_file()
    mode = path.stat().st_mode
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_articles_add_requires_csrf(app) -> None:
    client = app.test_client()
    resp = client.post(
        "/articles/add",
        data={"url": "https://example.com/no-csrf"},
    )
    assert resp.status_code == 400


def test_capture_created(app) -> None:
    client = app.test_client()
    resp = client.post(
        "/articles/capture",
        json={"url": "https://example.com/from-extension", "title": "From Firefox"},
        headers=_auth(app),
    )
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp.headers
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["outcome"] == "created"
    assert payload["status"] == "pending"
    assert payload["url"] == "https://example.com/from-extension"
    conn = init_db(Path(app.config["STATE_DIR"]) / "rollup.db")
    assert count_pending(conn) == 1
    item = get_by_id(conn, payload["id"])
    conn.close()
    assert item is not None
    assert item.display_title == "From Firefox"


def test_capture_duplicate_and_retried(app) -> None:
    client = app.test_client()
    headers = _auth(app)
    first = client.post(
        "/articles/capture",
        json={"url": "https://example.com/dup"},
        headers=headers,
    )
    assert first.get_json()["outcome"] == "created"
    second = client.post(
        "/articles/capture",
        json={"url": "https://example.com/dup"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.get_json()["outcome"] == "duplicate"

    conn = init_db(Path(app.config["STATE_DIR"]) / "rollup.db")
    item_id = first.get_json()["id"]
    mark_failed(conn, item_id, error_code="webpage_empty")
    conn.close()
    third = client.post(
        "/articles/capture",
        json={"url": "https://example.com/dup"},
        headers=headers,
    )
    assert third.status_code == 200
    body = third.get_json()
    assert body["outcome"] == "retried"
    assert body["status"] == "pending"


def test_capture_rejects_invalid_and_ssrf_urls(app) -> None:
    client = app.test_client()
    headers = _auth(app)
    invalid = client.post(
        "/articles/capture",
        json={"url": "ftp://example.com/x"},
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "url_invalid"

    ssrf = client.post(
        "/articles/capture",
        json={"url": "https://127.0.0.1/secret"},
        headers=headers,
    )
    assert ssrf.status_code == 400
    assert ssrf.get_json()["error"] == "url_ssrf"


def test_capture_auth_and_content_type(app) -> None:
    client = app.test_client()
    csrf = _csrf(client)

    missing = client.post(
        "/articles/capture",
        json={"url": "https://example.com/a"},
    )
    assert missing.status_code == 401
    assert missing.get_json()["error"] == "unauthorized"

    wrong = client.post(
        "/articles/capture",
        json={"url": "https://example.com/a"},
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert wrong.status_code == 401

    csrf_only = client.post(
        "/articles/capture",
        json={"url": "https://example.com/a", "csrf_token": csrf},
    )
    assert csrf_only.status_code == 401

    form = client.post(
        "/articles/capture",
        data={"url": "https://example.com/a", "csrf_token": csrf},
    )
    assert form.status_code == 415
    assert form.get_json()["error"] == "unsupported_media_type"

    get_resp = client.get("/articles/capture")
    assert get_resp.status_code == 405


def test_capture_rejects_non_loopback_host(app) -> None:
    app.config["WEB_ENFORCE_HOST"] = True
    app.config["WEB_BIND_HOST"] = "127.0.0.1"
    app.config["WEB_BIND_PORT"] = 8765
    client = app.test_client()
    resp = client.post(
        "/articles/capture",
        json={"url": "https://example.com/a"},
        headers={
            **_auth(app),
            "Host": "evil.example",
        },
    )
    assert resp.status_code == 400


def test_token_rotate_invalidates_old(app) -> None:
    client = app.test_client()
    old = app.config["EXTENSION_TOKEN"]
    csrf = _csrf(client)
    rotated = client.post(
        "/articles/token/rotate",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert rotated.status_code == 200
    new = app.config["EXTENSION_TOKEN"]
    assert new != old
    assert new.encode() in rotated.data
    assert old.encode() not in rotated.data

    stale = client.post(
        "/articles/capture",
        json={"url": "https://example.com/after-rotate"},
        headers={"Authorization": f"Bearer {old}"},
    )
    assert stale.status_code == 401
    fresh = client.post(
        "/articles/capture",
        json={"url": "https://example.com/after-rotate"},
        headers={"Authorization": f"Bearer {new}"},
    )
    assert fresh.status_code == 200
    assert fresh.get_json()["ok"] is True


def test_token_rotate_requires_csrf(app) -> None:
    client = app.test_client()
    resp = client.post("/articles/token/rotate")
    assert resp.status_code == 400


def test_capture_truncates_title_and_rejects_bad_payload(app) -> None:
    client = app.test_client()
    headers = _auth(app)
    long_title = "T" * 400
    created = client.post(
        "/articles/capture",
        json={"url": "https://example.com/long-title", "title": long_title},
        headers=headers,
    )
    assert created.status_code == 200
    item_id = created.get_json()["id"]
    conn = init_db(Path(app.config["STATE_DIR"]) / "rollup.db")
    item = get_by_id(conn, item_id)
    conn.close()
    assert item is not None
    assert item.display_title == "T" * 280

    bad_title = client.post(
        "/articles/capture",
        json={"url": "https://example.com/bad-title", "title": ["not", "a string"]},
        headers=headers,
    )
    assert bad_title.status_code == 400
    assert bad_title.get_json()["error"] == "invalid_json"

    empty = client.post(
        "/articles/capture",
        json={},
        headers=headers,
    )
    assert empty.status_code == 400
    assert empty.get_json()["error"] == "url_invalid"

    blank = client.post(
        "/articles/capture",
        json={"url": "   "},
        headers=headers,
    )
    assert blank.status_code == 400


def test_capture_malformed_authorization_and_errors_have_no_cors(app) -> None:
    client = app.test_client()
    scheme = client.post(
        "/articles/capture",
        json={"url": "https://example.com/a"},
        headers={"Authorization": "Token not-bearer"},
    )
    assert scheme.status_code == 401
    assert "Access-Control-Allow-Origin" not in scheme.headers

    empty_bearer = client.post(
        "/articles/capture",
        json={"url": "https://example.com/a"},
        headers={"Authorization": "Bearer "},
    )
    assert empty_bearer.status_code == 401
    assert "Access-Control-Allow-Origin" not in empty_bearer.headers

    options = client.options("/articles/capture")
    assert "Access-Control-Allow-Origin" not in options.headers
    assert "Access-Control-Allow-Headers" not in options.headers


def test_extension_token_refuses_open_mode_and_short_file(tmp_path: Path) -> None:
    open_state = tmp_path / "open"
    open_state.mkdir()
    open_path = open_state / "extension_token"
    open_path.write_text("y" * 32, encoding="utf-8")
    os.chmod(open_path, 0o644)
    with pytest.raises(WebSecretError):
        load_or_create_extension_token(open_state)

    short_state = tmp_path / "short"
    short_state.mkdir()
    short_path = short_state / "extension_token"
    short_path.write_text("tiny\n", encoding="utf-8")
    os.chmod(short_path, 0o600)
    with pytest.raises(WebSecretError):
        load_or_create_extension_token(short_state)


def test_extension_token_permissions_and_symlink(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    token = load_or_create_extension_token(state)
    path = state / "extension_token"
    assert path.exists()
    mode = path.stat().st_mode
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert load_or_create_extension_token(state) == token

    rotated = rotate_extension_token(state)
    assert rotated != token
    assert load_or_create_extension_token(state) == rotated

    bad = tmp_path / "state2"
    bad.mkdir()
    target = bad / "real"
    target.write_text("x" * 32, encoding="utf-8")
    os.chmod(target, 0o600)
    link = bad / "extension_token"
    link.symlink_to(target)
    with pytest.raises(WebSecretError):
        load_or_create_extension_token(bad)
    with pytest.raises(WebSecretError):
        rotate_extension_token(bad)
