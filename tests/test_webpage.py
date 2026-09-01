"""Tests for webpage article queue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rollup.article_html import extract_article_text_from_html, extract_title_from_html
from rollup.config import Config
from rollup.pipeline import (
    DiscoveryResult,
    evaluate_no_input,
    merge_webpage_parse,
    stage_discover,
    stage_parse_webpage,
)
from rollup.state import SCHEMA_VERSION, get_schema_version, init_db
from rollup.webpage.config import WEBPAGE_FOLDER_NAME
from rollup.webpage.fetch import WebpageFetchError, fetch_webpage
from rollup.webpage.parse import webpage_to_parsed_message
from rollup.webpage.queue import (
    count_pending,
    enqueue_url,
    get_by_id,
    list_by_status,
    load_for_digest,
    mark_failed,
    mark_ingested,
    remove_item,
    retry_item,
    store_fetched,
)
from rollup.webpage.url import (
    canonicalize_https_url,
    message_key_for_url,
    validate_queue_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


def _minimal_config(tmp_path: Path, **overrides) -> Config:
    base = dict(
        root=tmp_path / "Newsletters.sbd",
        mail_root=tmp_path,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=50_000,
        max_chars_for_llm=5_000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="test",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
        no_linkedin=True,
    )
    base.update(overrides)
    return Config(**base)


def test_schema_v13_webpage_body_cache(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION == 13
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(webpage_queue)").fetchall()
    }
    assert {"fetched_title", "body_text", "content_hash", "fetched_at"} <= cols
    conn.close()


def test_canonicalize_https_url() -> None:
    assert canonicalize_https_url("https://Example.com/path/") == "https://example.com/path"
    assert canonicalize_https_url("example.com/x") == "https://example.com/x"


def test_normalize_redirect_preserves_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    from rollup.webpage.url import normalize_redirect_url

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(None, None, None, None, ("93.184.216.34", 443))]

    monkeypatch.setattr("rollup.webpage.url.socket.getaddrinfo", fake_getaddrinfo)
    url = normalize_redirect_url(
        "https://example.com/air-pollution-in-greenlands-ports-what-ten-days-of-measurements-found-in-ilulissat/"
    )
    assert url.endswith("/")


def test_validate_queue_url_rejects_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "evil.example":
            return [(None, None, None, None, ("127.0.0.1", 443))]
        raise OSError("unknown")

    monkeypatch.setattr("rollup.webpage.url.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="Blocked"):
        validate_queue_url("https://evil.example/article")


def test_queue_crud(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    item = enqueue_url(conn, "https://example.com/article-one", display_title="One")
    assert item.status == "pending"
    assert count_pending(conn) == 1
    dup = enqueue_url(conn, "https://example.com/article-one")
    assert dup.id == item.id
    mark_failed(conn, item.id, error_code="webpage_empty", error_message="empty")
    assert list_by_status(conn, "failed")[0].status == "failed"
    retried = retry_item(conn, item.id)
    assert retried is not None and retried.status == "pending"
    mark_ingested(conn, [(item.id, message_key_for_url(item.url), "run-1")], ingested_at=datetime.now(timezone.utc))
    ingested = list_by_status(conn, "ingested")[0]
    assert ingested.status == "ingested"
    requeued = enqueue_url(conn, "https://example.com/article-one")
    assert requeued.status == "ingested"
    assert requeued.id == item.id
    remove_item(conn, requeued.id)
    assert count_pending(conn) == 0
    conn.close()


def test_extract_title_from_fixture() -> None:
    html = (FIXTURES / "article_squarespace.html").read_text(encoding="utf-8")
    title = extract_title_from_html(html)
    assert title
    body = extract_article_text_from_html(html)
    assert len(body) >= 200


def test_webpage_to_parsed_message_uses_save_date() -> None:
    when = datetime(2019, 1, 15, 12, 0, tzinfo=timezone.utc)
    url = "https://example.com/old-essay"
    msg = webpage_to_parsed_message(
        url=url,
        title="Old essay",
        body_text="word " * 50,
        saved_at=when,
        max_body_chars=50_000,
    )
    assert msg.folder_name == WEBPAGE_FOLDER_NAME
    assert msg.date_parsed == when
    assert msg.message_key == message_key_for_url(url)


def test_stage_parse_webpage_dry_run(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "state" / "rollup.db")
    item = enqueue_url(conn, "https://example.com/a")
    conn.close()
    config = _minimal_config(tmp_path)
    msgs, warnings, degraded, ingest = stage_parse_webpage(
        config,
        (item,),
        dry_run=True,
        generated_at=datetime.now(timezone.utc),
    )
    assert msgs == []
    assert any(w.code == "webpage_dry_run" for w in warnings)
    assert degraded is False
    assert ingest == []


def test_fetch_webpage_from_fixture_html() -> None:
    html = (FIXTURES / "article_squarespace.html").read_text(encoding="utf-8")
    session = MagicMock()
    response = MagicMock()
    response.is_redirect = False
    response.status_code = 200
    response.encoding = "utf-8"
    response.iter_content = lambda chunk_size: [html.encode("utf-8")]
    session.get.return_value = response
    with patch("rollup.webpage.fetch.assert_safe_fetch_host"):
        result = fetch_webpage("https://example.com/article", session)
    assert "SC22 outcomes" in result.body_text
    assert result.title


def test_evaluate_no_input_webpage_only_failure() -> None:
    discovery = DiscoveryResult(folders=(), linkedin_searches=(), webpage_items=(MagicMock(),))
    from rollup.pipeline import ParseCounts, ParseResult, StageWarning

    parse = ParseResult(
        messages=(),
        counts=ParseCounts(),
        warnings=(
            StageWarning(code="webpage_fetch_failed", message="fail", folder=WEBPAGE_FOLDER_NAME),
        ),
    )
    reason = evaluate_no_input(folders_include=(), discovery=discovery, parse=parse)
    assert reason is not None
    assert "Webpage fetch failed" in reason


def test_stage_discover_loads_pending(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    state = tmp_path / "state"
    conn = init_db(state / "rollup.db")
    enqueue_url(conn, "https://example.com/queued")
    conn.close()
    config = _minimal_config(tmp_path, root=root, state_dir=state)
    discovery = stage_discover(config)
    assert len(discovery.webpage_items) == 1


def test_stage_discover_skips_when_no_webpage(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    state = tmp_path / "state"
    conn = init_db(state / "rollup.db")
    enqueue_url(conn, "https://example.com/queued")
    conn.close()
    config = _minimal_config(tmp_path, root=root, state_dir=state, no_webpage=True)
    discovery = stage_discover(config)
    assert discovery.webpage_items == ()


def test_dry_run_does_not_change_queue_status(tmp_path: Path) -> None:
    from rollup.pipeline import run_digest
    from rollup.run_options import GroupingConfig, RunOptions

    root = tmp_path / "mail" / "Newsletters.sbd"
    root.mkdir(parents=True)
    state = tmp_path / "state"
    output = tmp_path / "output"
    logs = tmp_path / "logs"
    conn = init_db(state / "rollup.db")
    item = enqueue_url(conn, "https://example.com/dry-run-article")
    conn.close()
    config = _minimal_config(
        tmp_path,
        root=root,
        mail_root=tmp_path / "mail",
        output_dir=output,
        state_dir=state,
        log_dir=logs,
    )
    result = run_digest(
        config,
        RunOptions(dry_run=True, write_manifest=False),
        grouping=GroupingConfig(enabled=False),
    )
    assert result.status == "dry_run"
    conn = init_db(state / "rollup.db")
    assert count_pending(conn) == 1
    row = conn.execute(
        "SELECT status FROM webpage_queue WHERE id = ?", (item.id,)
    ).fetchone()
    conn.close()
    assert row[0] == "pending"


def test_merge_webpage_parse() -> None:
    from rollup.models import ParsedMessage
    from rollup.pipeline import ParseCounts, ParseResult

    mbox = ParseResult(messages=(), counts=ParseCounts(messages_parsed=0))
    msg = MagicMock(spec=ParsedMessage)
    merged = merge_webpage_parse(mbox, [msg], [])
    assert len(merged.messages) == 1


def _cache_body(conn, item_id: int, *, body: str = "cached article body " * 20) -> None:
    store_fetched(
        conn,
        item_id,
        title="Cached title",
        body_text=body,
        content_hash="hash",
        message_key="web:url:test",
        fetched_at=datetime.now(timezone.utc),
    )


def test_load_for_digest_includes_cached_in_window(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    for i in range(3):
        item = enqueue_url(
            conn,
            f"https://example.com/article-{i}",
            now=now,
        )
        _cache_body(conn, item.id)
    from rollup.config import compute_date_window

    start, end = compute_date_window(now, 7)
    items = load_for_digest(conn, window_start=start, window_end=end, fetch_limit=50)
    conn.close()
    assert len(items) == 3
    assert all(i.has_cached_body for i in items)


def test_load_for_digest_skips_cached_outside_window(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    old = now - timedelta(days=30)
    item = enqueue_url(conn, "https://example.com/old", now=old)
    _cache_body(conn, item.id)
    from rollup.config import compute_date_window

    start, end = compute_date_window(now, 7)
    items = load_for_digest(conn, window_start=start, window_end=end, fetch_limit=50)
    conn.close()
    assert items == ()


def test_stage_discover_loads_cached_in_lookback(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    state = tmp_path / "state"
    conn = init_db(state / "rollup.db")
    now = datetime.now(timezone.utc)
    for i in range(3):
        item = enqueue_url(conn, f"https://example.com/cached-{i}", now=now)
        _cache_body(conn, item.id)
    conn.close()
    config = _minimal_config(tmp_path, root=root, state_dir=state, lookback_days=7)
    discovery = stage_discover(config, generated_at=now)
    assert len(discovery.webpage_items) == 3


def test_stage_parse_webpage_uses_cached_body_without_fetch(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "state" / "rollup.db")
    item = enqueue_url(conn, "https://example.com/cached")
    _cache_body(conn, item.id, body="word " * 80)
    cached = get_by_id(conn, item.id)
    conn.close()
    assert cached is not None
    config = _minimal_config(tmp_path)
    with patch("rollup.webpage.fetch.fetch_webpage") as fetch:
        msgs, warnings, degraded, ingest = stage_parse_webpage(
            config,
            (cached,),
            dry_run=False,
            generated_at=datetime.now(timezone.utc),
        )
        fetch.assert_not_called()
    assert len(msgs) == 1
    assert msgs[0].date_parsed == cached.created_at
    assert "word" in msgs[0].body_text
    assert degraded is False
    assert ingest == [(cached.id, msgs[0].message_key)]
    assert warnings == []


def test_stage_parse_webpage_dry_run_still_emits_cached(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "state" / "rollup.db")
    item = enqueue_url(conn, "https://example.com/cached-dry")
    _cache_body(conn, item.id, body="word " * 80)
    cached = get_by_id(conn, item.id)
    conn.close()
    assert cached is not None
    config = _minimal_config(tmp_path)
    msgs, warnings, degraded, ingest = stage_parse_webpage(
        config,
        (cached,),
        dry_run=True,
        generated_at=datetime.now(timezone.utc),
    )
    assert len(msgs) == 1
    assert warnings == []
    assert ingest == [(cached.id, msgs[0].message_key)]
