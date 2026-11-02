"""Web UI shell contracts: CSP-safe templates, a11y landmarks, navigation, Home.

These tests guard the GUI hardening wave:
- no inline ``style=`` / ``on*=`` / ``<script>`` in templates (strict CSP)
- every page renders one ``h1``, a ``main#main`` landmark, a skip link, and
  a labelled primary nav with ``aria-current`` on the active item
- every form control is labelled
- post-action ``next`` redirects keep query string and anchor
- ``/theme.css`` publishes folder accents; ``/`` lands on Home by default
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.folder_theme import FolderThemeOverride
from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.source_registry import ensure_source_anchor
from rollup.state import init_db
from rollup.utc import format_utc
from rollup.web.app import create_app
from rollup.web.theme_css import render_theme_css
from rollup.web_ids import encode_opaque

NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "rollup" / "web" / "templates"

_INLINE_STYLE = re.compile(r"\sstyle\s*=", re.IGNORECASE)
_INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>", re.IGNORECASE)
_STYLE_TAG = re.compile(r"<style[\s>]", re.IGNORECASE)


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


@pytest.mark.parametrize("path", _templates(), ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_template_has_no_csp_hostile_markup(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not _INLINE_STYLE.search(text), f"{path.name}: inline style= is blocked by CSP"
    assert not _INLINE_HANDLER.search(text), f"{path.name}: inline on*= handler is blocked by CSP"
    assert not _INLINE_SCRIPT.search(text), f"{path.name}: inline <script> is blocked by CSP"
    assert not _STYLE_TAG.search(text), f"{path.name}: <style> is blocked by CSP"


class _PageAudit(HTMLParser):
    """Collect landmarks and form-control labelling from a rendered page."""

    def __init__(self) -> None:
        super().__init__()
        self.h1 = 0
        self.main_ids: list[str] = []
        self.skip_link = False
        self.nav_labels: list[str] = []
        self.aria_current: list[str] = []
        self.label_for: set[str] = set()
        self.open_labels = 0
        self.unlabelled: list[str] = []
        self.ids: list[str] = []
        self._in_a_href: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(a["id"])
        if tag == "h1":
            self.h1 += 1
        elif tag == "main":
            self.main_ids.append(a.get("id") or "")
        elif tag == "nav":
            self.nav_labels.append(a.get("aria-label") or "")
        elif tag == "a":
            self._in_a_href = a.get("href")
            if a.get("aria-current") == "page":
                self.aria_current.append(a.get("href") or "")
            if a.get("class") and "skip-link" in a["class"]:
                self.skip_link = True
        elif tag == "label":
            self.open_labels += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag in ("input", "select", "textarea"):
            if a.get("type") in ("hidden", "submit", "button"):
                return
            labelled = (
                self.open_labels > 0
                or bool(a.get("aria-label"))
                or bool(a.get("aria-labelledby"))
                or (a.get("id") and a["id"] in self.label_for)
            )
            if not labelled:
                # Allow later <label for=...>; checked in finish().
                self.unlabelled.append(a.get("id") or f"{tag}[name={a.get('name')}]")

    def handle_endtag(self, tag):
        if tag == "label":
            self.open_labels = max(0, self.open_labels - 1)
        elif tag == "a":
            self._in_a_href = None

    def finish(self) -> list[str]:
        return [u for u in self.unlabelled if u not in self.label_for]


def _audit(html: str) -> _PageAudit:
    p = _PageAudit()
    p.feed(html)
    return p


@pytest.fixture
def env(tmp_path: Path):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    conn = init_db(db)
    ensure_source_anchor(conn, "from:a@example.com", now=NOW, display_name_observed="A")
    ensure_source_anchor(conn, "from:extra@example.com", now=NOW, display_name_observed="Extra")
    conn.commit()
    conn.close()
    iso = format_utc(NOW)
    run_id = str(uuid.uuid4())
    (out / "x.md").write_text("# digest", encoding="utf-8")
    (out / "x.html").write_text("<html>hi</html>", encoding="utf-8")
    entries = [
        IndexEntry(
            message_key=f"mid:msg{i}@x",
            source_key_observed="from:a@example.com",
            group_id=None,
            group_type=None,
            group_display_name=None,
            section_key="tech",
            section_position=0,
            group_position=None,
            entry_position=i,
            display_position=i,
            folder_name="Tech News" if i % 2 else "enviro",
            subject=f"Hello {i}",
            sender="A",
            date_parsed=iso,
            date_raw="",
            newsletter_type="essay",
            summary="Sum",
            summary_source="none",
            primary_link="https://example.com/",
            links_json='{"v":1,"items":[{"href":"https://example.com/","label":"Ex"}]}',
        )
        for i in range(3)
    ]
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
        messages_included=3,
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
        entries=entries,
        expected_entry_count=3,
    )
    index_rollup_run(db, payload)
    app = create_app(state_dir=state, output_dir=out, mail_root=tmp_path / "mail", testing=True)
    return app, run_id


PAGES = [
    "/home",
    "/rollups",
    "/search",
    "/search?q=hello",
    "/run",
    "/articles",
    "/linkedin",
    "/reddit",
    "/rss",
    "/settings",
    "/sources",
    "/sources/registry",
    "/admin",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_shell_landmarks(env, path: str) -> None:
    app, _run_id = env
    client = app.test_client()
    resp = client.get(path)
    assert resp.status_code == 200, path
    html = resp.get_data(as_text=True)
    audit = _audit(html)
    assert audit.h1 == 1, f"{path}: expected exactly one h1, got {audit.h1}"
    assert "main" in audit.main_ids, f"{path}: missing <main id=main>"
    assert audit.skip_link, f"{path}: missing skip link"
    assert "Primary" in audit.nav_labels, f"{path}: primary nav lacks aria-label"
    assert audit.aria_current, f"{path}: no aria-current=page in nav"
    assert audit.finish() == [], f"{path}: unlabelled controls {audit.finish()}"
    assert '<meta name="color-scheme" content="light dark">' in html
    ids = [i for i in audit.ids if i]
    assert len(ids) == len(set(ids)), f"{path}: duplicate ids {sorted(set(i for i in ids if ids.count(i) > 1))}"


def test_rollup_detail_and_reader_shell(env) -> None:
    app, run_id = env
    client = app.test_client()
    resp = client.get(f"/rollups/{run_id}")
    assert resp.status_code == 200
    audit = _audit(resp.get_data(as_text=True))
    assert audit.h1 == 1
    assert audit.finish() == []
    html = resp.get_data(as_text=True)
    # Entry cards are anchorable and carry the folder accent class.
    assert f'id="entry-{encode_opaque("mid:msg0@x")}"' in html
    assert "folder-accent-tech-news" in html
    assert "folder-accent-enviro" in html
    # Human dates, not ISO timestamps, in card meta.
    assert "2024-06-01T" not in html.split("entry-meta", 1)[1][:300]


def test_next_preserves_query_and_anchor(env) -> None:
    app, run_id = env
    client = app.test_client()
    page = client.get(f"/rollups/{run_id}?page=1&show_dismissed=1")
    html = page.get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m
    token = m.group(1)
    enc = encode_opaque("mid:msg1@x")
    nxt = f"/rollups/{run_id}?page=1&show_dismissed=1#entry-{enc}"
    assert f'name="next" value="{nxt.replace("&", "&amp;")}"' in html
    r = client.post(
        f"/messages/{enc}/read",
        data={"csrf_token": token, "action": "read", "next": nxt},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith(nxt)


def test_registry_pager_encodes_query(env) -> None:
    app, _run_id = env
    client = app.test_client()
    # "a@" matches the fixture source and contains a character that must be encoded.
    r = client.get("/sources/registry?q=a%40&filter=all&sort=key_asc&page_size=1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'value="a@"' in html  # filter form echoes the raw value
    assert 'href="?page=2&amp;sort=key_asc&amp;filter=all&amp;q=a%40"' in html
    assert "q=a@" not in html


def test_sources_tabs_share_one_page(env) -> None:
    app, _run_id = env
    client = app.test_client()
    quality = client.get("/sources").get_data(as_text=True)
    registry = client.get("/sources/registry").get_data(as_text=True)
    assert 'href="/sources" aria-current="page"' in quality
    assert 'href="/sources/registry" aria-current="page"' in registry
    assert 'aria-sort="descending"' in quality or "No sources" in quality


def test_root_lands_on_home_by_default(env) -> None:
    app, _run_id = env
    client = app.test_client()
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/home")
    app.config["UI_LANDING_PAGE"] = "archive"
    r = client.get("/", follow_redirects=False)
    assert r.headers["Location"].endswith("/rollups")


def test_home_is_read_only_and_shows_latest(env) -> None:
    app, run_id = env
    client = app.test_client()
    r = client.get("/home")
    html = r.get_data(as_text=True)
    assert "Latest briefing" in html
    assert f"/rollups/{run_id}" in html
    assert "no-store" in (r.headers.get("Cache-Control") or "")


def test_theme_css_route_and_renderer(env) -> None:
    app, _run_id = env
    client = app.test_client()
    r = client.get("/theme.css")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/css")
    body = r.get_data(as_text=True)
    assert ".folder-accent-tech-news { --folder-accent: #" in body
    assert ".folder-accent-enviro { --folder-accent: #" in body

    # Overrides win; unsafe colours and slugs are dropped.
    css = render_theme_css(
        {
            "tech": FolderThemeOverride(accent="#123456"),
            "bad": FolderThemeOverride(accent="red; } body { display:none"),
        },
        None,
    )
    assert ".folder-accent-tech { --folder-accent: #123456; }" in css
    assert "display:none" not in css
    assert "folder-accent-bad" not in css


def test_flash_categories_render_with_roles(env) -> None:
    app, _run_id = env
    client = app.test_client()
    page = client.get("/articles")
    m = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
    assert m
    token = m.group(1).decode()
    r = client.post("/articles/add", data={"csrf_token": token, "url": ""}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert 'class="flash flash-error" role="alert"' in html
