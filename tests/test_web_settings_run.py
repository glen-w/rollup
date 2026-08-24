"""Web Configuration Centre and Run Studio route smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.state import init_db
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
    (root / "tech").write_text("From: a@b.com\nSubject: Hi\n\nBody\n", encoding="utf-8")
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
                'effort = "balanced"',
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


def test_settings_get(app) -> None:
    application, _cfg = app
    client = application.test_client()
    resp = client.get("/settings/")
    assert resp.status_code == 200
    assert b"Configuration Centre" in resp.data
    assert b"First-run checklist" in resp.data
    assert b"effort_model_balanced_rough" in resp.data
    assert b"effort_model_high_max" in resp.data


def test_run_studio_get(app) -> None:
    application, _cfg = app
    client = application.test_client()
    resp = client.get("/run/")
    assert resp.status_code == 200
    assert b"Run Studio" in resp.data
    assert b"Effective run" in resp.data
    assert b"rollup digest" in resp.data or b"digest" in resp.data
    assert b"use_single_model" in resp.data
    assert b"Use a single model for this run" in resp.data


def test_settings_preview_requires_csrf(app) -> None:
    application, _cfg = app
    client = application.test_client()
    resp = client.post(
        "/settings/preview",
        data={"lookback_days": "3"},
        follow_redirects=False,
    )
    assert resp.status_code in (400, 302)


def test_settings_preview_and_save(app) -> None:
    application, cfg = app
    client = application.test_client()
    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "test-csrf-token"
    resp = client.post(
        "/settings/preview",
        data={
            "csrf_token": "test-csrf-token",
            "mail_root": str(Path(application.config["MAIL_ROOT"])),
            "root": str(Path(application.config["NEWSLETTER_ROOT"])),
            "output_dir": str(Path(application.config["OUTPUT_DIR"])),
            "state_dir": str(Path(application.config["STATE_DIR"])),
            "log_dir": str(Path(application.config["STATE_DIR"]) / "logs"),
            "lookback_days": "3",
            "effort": "light",
            "ollama": "0",
            "no_grouping": "0",
            "output_mode": "none",
            "profile": "weekly",
            "landing_page": "archive",
            "preferred_view": "html",
            "onboarding_complete": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"Confirm configuration save" in resp.data
    assert b"lookback_days" in resp.data or b"lookback" in resp.data

    # Extract confirm token from HTML
    html = resp.data.decode("utf-8")
    assert 'name="confirm_token"' in html
    # Re-fetch csrf after rotate
    with client.session_transaction() as sess:
        token = sess[CSRF_SESSION_KEY]

    # Pull hidden fields roughly
    import re

    confirm = re.search(r'name="confirm_token" value="([^"]+)"', html)
    preview_fp = re.search(r'name="preview_fp" value="([^"]+)"', html)
    assert confirm and preview_fp
    save_resp = client.post(
        "/settings/save",
        data={
            "csrf_token": token,
            "confirm_token": confirm.group(1),
            "preview_fp": preview_fp.group(1),
            "mail_root": str(Path(application.config["MAIL_ROOT"])),
            "root": str(Path(application.config["NEWSLETTER_ROOT"])),
            "output_dir": str(Path(application.config["OUTPUT_DIR"])),
            "state_dir": str(Path(application.config["STATE_DIR"])),
            "log_dir": str(Path(application.config["STATE_DIR"]) / "logs"),
            "lookback_days": "3",
            "effort": "light",
            "ollama": "0",
            "no_grouping": "0",
            "output_mode": "none",
            "profile": "weekly",
            "landing_page": "archive",
            "preferred_view": "html",
            "onboarding_complete": "1",
        },
        follow_redirects=True,
    )
    assert save_resp.status_code == 200
    text = cfg.read_text(encoding="utf-8")
    assert "lookback_days = 3" in text
    assert 'effort = "light"' in text


def test_preferred_view_highlighted_on_archive(app) -> None:
    import uuid
    from datetime import datetime, timezone

    from rollup.run_index import RunIndexPayload, index_rollup_run
    from rollup.utc import format_utc

    application, cfg = app
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text + '\n[ui]\npreferred_view = "markdown"\n', encoding="utf-8")
    from rollup.web.app import refresh_config_derived

    refresh_config_derived(application)
    state = Path(application.config["STATE_DIR"])
    out = Path(application.config["OUTPUT_DIR"])
    (out / "x.md").write_text("# digest", encoding="utf-8")
    (out / "x.html").write_text("<html></html>", encoding="utf-8")
    now = format_utc(datetime(2024, 6, 1, tzinfo=timezone.utc))
    run_id = str(uuid.uuid4())
    payload = RunIndexPayload(
        run_id=run_id,
        started_at=now,
        completed_at=now,
        status="success",
        mode="manual",
        rollup_version="0.6.2",
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
        entries=[],
        expected_entry_count=0,
    )
    index_rollup_run(state / "rollup.db", payload)
    from rollup.web.app import refresh_config_derived

    refresh_config_derived(application)
    client = application.test_client()
    resp = client.get("/rollups")
    assert resp.status_code == 200
    assert b"Open preferred (Markdown)" in resp.data


def test_run_busy_returns_503(app, monkeypatch: pytest.MonkeyPatch) -> None:
    from rollup.web.routes import run as run_routes

    application, _cfg = app
    client = application.test_client()
    monkeypatch.setattr(run_routes, "is_busy", lambda: True)
    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "csrf-busy"
    resp = client.post(
        "/run/start",
        data={"csrf_token": "csrf-busy", "profile": "weekly"},
        follow_redirects=False,
    )
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "5"


def test_run_dry_run_subprocess(app, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run POST starts subprocess path; mock runner to avoid full digest."""
    from rollup.web import run_runner
    from rollup.web.run_runner import ActiveRun

    application, _cfg = app
    client = application.test_client()

    def fake_start(argv, *, dry_run, cwd=None):
        run = ActiveRun(
            run_id="test-dry",
            argv=list(argv),
            dry_run=dry_run,
            started_at=0.0,
            status="dry_run",
            exit_code=0,
        )
        run.log_lines.append("Folders scanned: 1")
        run_runner._active = run  # noqa: SLF001
        return run

    monkeypatch.setattr(run_runner, "start_digest_subprocess", fake_start)
    monkeypatch.setattr(
        run_runner, "wait_until_idle", lambda timeout=600: run_runner.get_active_run()
    )
    monkeypatch.setattr(run_runner, "is_busy", lambda: False)

    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "csrf-dry"
    resp = client.post(
        "/run/dry-run",
        data={
            "csrf_token": "csrf-dry",
            "profile": "weekly",
            "ollama": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/run/result" in (resp.headers.get("Location") or "")
    status = client.get("/run/status").get_json()
    assert status["status"] == "dry_run"
    joined = " ".join(status.get("argv") or [])
    assert "digest" in joined
    assert "--dry-run" in joined


def test_landing_page_run_redirect(app) -> None:
    application, _cfg = app
    application.config["UI_LANDING_PAGE"] = "run"
    client = application.test_client()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/run" in (resp.headers.get("Location") or "")


def test_settings_saves_effort_models(app) -> None:
    application, cfg = app
    client = application.test_client()
    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "test-csrf-token"
    base = {
        "csrf_token": "test-csrf-token",
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
        "effort_model_high_rough": "my-rough:latest",
        "effort_model_high_standard": "",
        "effort_model_high_deep": "",
        "effort_model_high_max": "",
        "effort_model_high_ollama_model": "my-group:latest",
        "effort_model_high_final_review_model": "",
        "effort_model_light_rough": "",
        "effort_model_balanced_rough": "",
    }
    resp = client.post("/settings/preview", data=base, follow_redirects=False)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    import re

    confirm = re.search(r'name="confirm_token" value="([^"]+)"', html)
    preview_fp = re.search(r'name="preview_fp" value="([^"]+)"', html)
    assert confirm and preview_fp
    with client.session_transaction() as sess:
        token = sess[CSRF_SESSION_KEY]
    save_resp = client.post(
        "/settings/save",
        data={
            **base,
            "csrf_token": token,
            "confirm_token": confirm.group(1),
            "preview_fp": preview_fp.group(1),
        },
        follow_redirects=True,
    )
    assert save_resp.status_code == 200
    text = cfg.read_text(encoding="utf-8")
    assert "my-rough:latest" in text
    assert "my-group:latest" in text


def test_run_studio_single_model_in_argv(app, monkeypatch: pytest.MonkeyPatch) -> None:
    from rollup.web import run_runner
    from rollup.web.run_runner import ActiveRun
    from rollup.web.routes import run as run_routes

    application, _cfg = app
    client = application.test_client()
    captured: dict[str, list[str]] = {}

    def fake_start(argv, *, dry_run, cwd=None):
        captured["argv"] = list(argv)
        run = ActiveRun(
            run_id="test-single",
            argv=list(argv),
            dry_run=dry_run,
            started_at=0.0,
            status="dry_run",
            exit_code=0,
        )
        run_runner._active = run  # noqa: SLF001
        return run

    monkeypatch.setattr(run_routes, "start_digest_subprocess", fake_start)
    monkeypatch.setattr(
        run_runner, "wait_until_idle", lambda timeout=600: run_runner.get_active_run()
    )
    monkeypatch.setattr(run_runner, "is_busy", lambda: False)

    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "csrf-single"
    resp = client.post(
        "/run/dry-run",
        data={
            "csrf_token": "csrf-single",
            "profile": "weekly",
            "ollama": "0",
            "use_single_model": "1",
            "single_model": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    argv = captured.get("argv") or []
    assert "--single-model" in argv
    assert "qwen2.5:7b" in argv
    assert "--ollama" in argv


def test_run_studio_ignores_single_model_unless_checked(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rollup.web import run_runner
    from rollup.web.run_runner import ActiveRun
    from rollup.web.routes import run as run_routes

    application, _cfg = app
    client = application.test_client()
    captured: dict[str, list[str]] = {}

    def fake_start(argv, *, dry_run, cwd=None):
        captured["argv"] = list(argv)
        run = ActiveRun(
            run_id="test-unchecked",
            argv=list(argv),
            dry_run=dry_run,
            started_at=0.0,
            status="dry_run",
            exit_code=0,
        )
        run_runner._active = run  # noqa: SLF001
        return run

    monkeypatch.setattr(run_routes, "start_digest_subprocess", fake_start)
    monkeypatch.setattr(
        run_runner, "wait_until_idle", lambda timeout=600: run_runner.get_active_run()
    )
    monkeypatch.setattr(run_runner, "is_busy", lambda: False)

    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "csrf-unchecked"
    resp = client.post(
        "/run/dry-run",
        data={
            "csrf_token": "csrf-unchecked",
            "profile": "weekly",
            "single_model": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    argv = captured.get("argv") or []
    assert "--single-model" not in argv


def test_run_studio_get_does_not_list_ollama(app, monkeypatch: pytest.MonkeyPatch) -> None:
    from rollup.web.routes import run as run_routes

    def boom(*_a, **_k):
        raise AssertionError("GET /run must not contact Ollama")

    monkeypatch.setattr(run_routes, "list_ollama_models", boom)
    application, _cfg = app
    client = application.test_client()
    resp = client.get("/run/")
    assert resp.status_code == 200


def test_run_ollama_models_lists_tags(app, monkeypatch: pytest.MonkeyPatch) -> None:
    from rollup.web.routes import run as run_routes

    monkeypatch.setattr(
        run_routes, "list_ollama_models", lambda *_a, **_k: ["llama3.2:3b", "qwen2.5:7b"]
    )
    application, _cfg = app
    client = application.test_client()
    with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "csrf-tags"
    resp = client.post(
        "/run/ollama-models",
        data={"csrf_token": "csrf-tags"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["models"] == ["llama3.2:3b", "qwen2.5:7b"]
