"""Expanded web route and safety contract tests."""

from __future__ import annotations

import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.interaction import get_interaction
from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.source_registry import ensure_source_anchor, set_overrides
from rollup.state import init_db
from rollup.utc import format_utc
from rollup.web.app import create_app
from rollup.web.secrets import WebSecretError, load_or_create_secret
from rollup.web_ids import encode_opaque


NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _csrf(client, path: str) -> str:
    page = client.get(path)
    assert page.status_code == 200
    m = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
    assert m
    return m.group(1).decode()


@pytest.fixture
def env(tmp_path: Path):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    conn = init_db(db)
    iso = format_utc(NOW)
    ensure_source_anchor(conn, "from:a@example.com", now=NOW, display_name_observed="A")
    set_overrides(
        conn,
        "from:a@example.com",
        updates={"display_name": "Alpha", "newsletter_type": "essay"},
        updated_by="cli",
        now=NOW,
    )
    conn.commit()
    conn.close()

    run_id = str(uuid.uuid4())
    (out / "x.md").write_text("# digest", encoding="utf-8")
    (out / "x.html").write_text("<html>hi</html>", encoding="utf-8")
    (state / "manifests").mkdir()
    man = state / "manifests" / "m.json"
    man.write_text('{"ok": true}', encoding="utf-8")

    payload = RunIndexPayload(
        run_id=run_id,
        started_at=iso,
        completed_at=iso,
        status="success",
        mode="manual",
        rollup_version="0.5.0",
        manifest_schema_version=2,
        report_schema_version=1,
        stats_completeness="full",
        window_start=iso,
        window_end=iso,
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
        manifest_relpath="manifests/m.json",
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
                subject='Hello <script>alert(1)</script>',
                sender="A",
                date_parsed=iso,
                date_raw="",
                newsletter_type="essay",
                summary="Sum <b>bold</b>",
                summary_source="none",
                primary_link="https://example.com/",
                links_json='{"v":1,"items":[{"href":"https://example.com/","label":"Ex"}]}',
            )
        ],
        expected_entry_count=1,
    )
    index_rollup_run(db, payload)
    app = create_app(state_dir=state, output_dir=out, testing=True)
    return app, run_id, db


def test_get_detail_does_not_mark_read(env):
    app, run_id, db = env
    client = app.test_client()
    r = client.get(f"/rollups/{run_id}")
    assert r.status_code == 200
    conn = init_db(db)
    assert not get_interaction(conn, "mid:msg@x").is_read
    conn.close()


def test_escaping_hostile_markup(env):
    app, run_id, _db = env
    client = app.test_client()
    r = client.get(f"/rollups/{run_id}")
    assert b"<script>alert(1)</script>" not in r.data
    assert b"&lt;script&gt;" in r.data or b"&#" in r.data
    assert b"Sum <b>bold</b>" not in r.data


def test_security_headers(env):
    app, _run_id, _db = env
    client = app.test_client()
    r = client.get("/rollups")
    assert "default-src" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "no-referrer"
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_csrf_missing_rejected(env):
    app, run_id, _db = env
    client = app.test_client()
    enc = encode_opaque("mid:msg@x")
    r = client.post(
        f"/messages/{enc}/read",
        data={"action": "read", "next": f"/rollups/{run_id}"},
    )
    assert r.status_code == 400


def test_mark_read_save_dismiss_with_csrf(env):
    app, run_id, db = env
    client = app.test_client()
    token = _csrf(client, f"/rollups/{run_id}")
    enc = encode_opaque("mid:msg@x")
    for path, data in (
        (f"/messages/{enc}/read", {"action": "read"}),
        (f"/messages/{enc}/save", {"action": "save"}),
        (f"/messages/{enc}/dismiss", {"action": "dismiss"}),
    ):
        r = client.post(
            path,
            data={**data, "csrf_token": token, "next": f"/rollups/{run_id}"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        token = _csrf(client, f"/rollups/{run_id}?show_dismissed=1")
    conn = init_db(db)
    st = get_interaction(conn, "mid:msg@x")
    assert st.is_read and st.is_saved and st.is_dismissed
    conn.close()


def test_invalid_ids_404(env):
    app, _run_id, _db = env
    client = app.test_client()
    assert client.get("/rollups/not-a-uuid").status_code == 404
    assert client.get("/sources/!!!").status_code == 404


def test_artifacts_html_manifest_and_traversal(env):
    app, run_id, db = env
    client = app.test_client()
    assert client.get(f"/artifacts/{run_id}/html").status_code == 200
    assert client.get(f"/artifacts/{run_id}/manifest").status_code == 200
    # Craft unsafe relpath in DB
    conn = init_db(db)
    conn.execute(
        "UPDATE rollup_runs SET markdown_relpath = ? WHERE run_id = ?",
        ("../etc/passwd", run_id),
    )
    conn.commit()
    conn.close()
    assert client.get(f"/artifacts/{run_id}/md").status_code == 400


def test_policy_partial_and_conflict(env):
    app, _run_id, db = env
    client = app.test_client()
    enc = encode_opaque("from:a@example.com")
    page = client.get(f"/sources/{enc}")
    assert page.status_code == 200
    token = _csrf(client, f"/sources/{enc}")
    conn = init_db(db)
    from rollup.source_registry import load_overrides

    current = load_overrides(conn, "from:a@example.com")
    token_at = current.updated_at
    conn.close()
    # Partial: only display_name field listed — newsletter_type must remain
    r = client.post(
        f"/sources/{enc}/policy",
        data={
            "csrf_token": token,
            "fields": ["display_name"],
            "display_name": "Beta",
            "overrides_updated_at": token_at,
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    conn = init_db(db)
    ov = load_overrides(conn, "from:a@example.com")
    assert ov.display_name == "Beta"
    assert ov.newsletter_type == "essay"
    stale = ov.updated_at
    # Change underneath
    set_overrides(
        conn,
        "from:a@example.com",
        updates={"display_name": "Gamma"},
        updated_by="cli",
        now=datetime(2024, 6, 2, tzinfo=timezone.utc),
    )
    conn.close()
    token = _csrf(client, f"/sources/{enc}")
    conflict = client.post(
        f"/sources/{enc}/policy",
        data={
            "csrf_token": token,
            "fields": ["display_name"],
            "display_name": "Delta",
            "overrides_updated_at": stale,
        },
    )
    assert conflict.status_code == 409

    # Omitting the token must also conflict once overrides exist.
    conn = init_db(db)
    fresh = load_overrides(conn, "from:a@example.com").updated_at
    conn.close()
    assert fresh is not None
    token = _csrf(client, f"/sources/{enc}")
    missing = client.post(
        f"/sources/{enc}/policy",
        data={
            "csrf_token": token,
            "fields": ["display_name"],
            "display_name": "Epsilon",
        },
    )
    assert missing.status_code == 409
    conn = init_db(db)
    assert load_overrides(conn, "from:a@example.com").display_name == "Gamma"
    conn.close()


def test_message_next_rejects_protocol_relative(env):
    app, run_id, _db = env
    client = app.test_client()
    enc = encode_opaque("mid:msg@x")
    token = _csrf(client, f"/rollups/{run_id}")
    for bad in ("//evil.example/phish", "https://evil.example/", "/\\evil"):
        r = client.post(
            f"/messages/{enc}/read",
            data={"csrf_token": token, "next": bad, "action": "read"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        loc = r.headers["Location"]
        assert loc.startswith("/")
        assert not loc.startswith("//")
        assert "evil" not in loc
    ok = client.post(
        f"/messages/{enc}/read",
        data={"csrf_token": token, "next": f"/rollups/{run_id}", "action": "read"},
        follow_redirects=False,
    )
    assert ok.status_code == 302
    assert f"/rollups/{run_id}" in ok.headers["Location"]


def test_bind_localhost_ok():
    from rollup.web.bind import BindError, validate_bind_host

    assert validate_bind_host("localhost") == "localhost"
    with pytest.raises(BindError):
        validate_bind_host("example.com")


def test_web_secret_permissions_and_symlink(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    secret = load_or_create_secret(state)
    path = state / "web_secret"
    assert path.exists()
    mode = path.stat().st_mode
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert load_or_create_secret(state) == secret

    bad = tmp_path / "state2"
    bad.mkdir()
    target = bad / "real"
    target.write_bytes(b"x" * 32)
    link = bad / "web_secret"
    link.symlink_to(target)
    with pytest.raises(WebSecretError):
        load_or_create_secret(bad)
