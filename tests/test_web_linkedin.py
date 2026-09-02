"""Web LinkedIn named-search configuration route tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.state import init_db
from rollup.user_config import load_toml_file
from rollup.web.app import create_app
from rollup.web.csrf import CSRF_SESSION_KEY
from rollup.web.run_runner import clear_finished_for_tests

WATCHLIST_URL = (
    Path(__file__).parent / "fixtures" / "linkedin" / "watchlist_url.txt"
).read_text(encoding="utf-8").strip()


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
                "[linkedin]",
                "enabled = true",
                'layout = "per_search"',
                "",
                "[linkedin.searches.watchlist]",
                f'url = "{WATCHLIST_URL}"',
                'display_name = "LinkedIn watchlist"',
                "enabled = true",
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


def _base_settings_form(application, **overrides):
    data = {
        "mail_root": str(Path(application.config["MAIL_ROOT"])),
        "root": str(Path(application.config["NEWSLETTER_ROOT"])),
        "output_dir": str(Path(application.config["OUTPUT_DIR"])),
        "state_dir": str(Path(application.config["STATE_DIR"])),
        "log_dir": str(Path(application.config["STATE_DIR"]) / "logs"),
        "lookback_days": "7",
        "effort": "balanced",
        "ollama": "0",
        "no_grouping": "0",
        "output_mode": "none",
        "profile": "weekly",
        "landing_page": "archive",
        "preferred_view": "html",
        "onboarding_complete": "1",
    }
    data.update(overrides)
    return data


def _preview_and_save(client, application, form: dict) -> None:
    import re

    token = _csrf(client)
    resp = client.post(
        "/settings/preview",
        data={**form, "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200, resp.data.decode("utf-8", errors="replace")
    html = resp.data.decode("utf-8")
    confirm = re.search(r'name="confirm_token" value="([^"]+)"', html)
    preview_fp = re.search(r'name="preview_fp" value="([^"]+)"', html)
    assert confirm and preview_fp
    with client.session_transaction() as sess:
        token = sess[CSRF_SESSION_KEY]
    save_resp = client.post(
        "/settings/save",
        data={
            **form,
            "csrf_token": token,
            "confirm_token": confirm.group(1),
            "preview_fp": preview_fp.group(1),
        },
        follow_redirects=True,
    )
    assert save_resp.status_code == 200


def test_linkedin_index_lists_existing(app) -> None:
    application, _cfg = app
    client = application.test_client()
    resp = client.get("/linkedin")
    assert resp.status_code == 200
    assert b"Add named search" in resp.data
    assert b"LinkedIn watchlist" in resp.data
    assert b"Add another search" in resp.data
    assert b"per_search (one section per named search)" in resp.data


def test_linkedin_save_adds_named_search(app) -> None:
    application, cfg = app
    client = application.test_client()
    token = _csrf(client)
    resp = client.post(
        "/linkedin/save",
        data={
            "csrf_token": token,
            "linkedin_enabled": "1",
            "linkedin_article_fetch": "1",
            "linkedin_layout": "per_search",
            "search_original_slug": "watchlist",
            "search_slug": "general",
            "search_display_name": "General",
            "search_url": WATCHLIST_URL,
            "search_enabled_watchlist": "1",
            "add_display_name": "BBNJ",
            "add_slug": "",
            "add_url": WATCHLIST_URL,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    loaded = load_toml_file(cfg)
    assert loaded.linkedin.enabled is True
    assert loaded.linkedin.layout == "per_search"
    assert set(loaded.linkedin.searches) == {"general", "bbnj"}
    assert loaded.linkedin.searches["general"].display_name == "General"
    assert loaded.linkedin.searches["bbnj"].display_name == "BBNJ"
    assert loaded.linkedin.searches["bbnj"].enabled is True


def test_linkedin_save_requires_csrf(app) -> None:
    application, _cfg = app
    client = application.test_client()
    resp = client.post(
        "/linkedin/save",
        data={"linkedin_enabled": "1", "add_url": WATCHLIST_URL},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_linkedin_save_rejects_keyword_url(app) -> None:
    application, cfg = app
    client = application.test_client()
    token = _csrf(client)
    resp = client.post(
        "/linkedin/save",
        data={
            "csrf_token": token,
            "linkedin_enabled": "1",
            "linkedin_article_fetch": "1",
            "linkedin_layout": "per_search",
            "search_original_slug": "watchlist",
            "search_slug": "watchlist",
            "search_display_name": "LinkedIn watchlist",
            "search_url": WATCHLIST_URL,
            "search_enabled_watchlist": "1",
            "add_display_name": "Keywords",
            "add_url": "https://www.linkedin.com/search/results/content/?keywords=ocean",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"fromMember" in resp.data
    loaded = load_toml_file(cfg)
    assert set(loaded.linkedin.searches) == {"watchlist"}


def test_settings_save_preserves_linkedin_searches(app) -> None:
    application, cfg = app
    client = application.test_client()
    form = _base_settings_form(application, linkedin_enabled="1")
    _preview_and_save(client, application, form)
    loaded = load_toml_file(cfg)
    assert "watchlist" in loaded.linkedin.searches
    assert loaded.linkedin.searches["watchlist"].display_name == "LinkedIn watchlist"
    assert loaded.linkedin.enabled is True
