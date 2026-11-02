"""Offline coverage for RSS, Kindle, continuity, attempts, and Gmail history."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rollup.addons.kindle.config import KindleConfig, parse_kindle_config
from rollup.addons.kindle.writer import KindleOutputWriter
from rollup.attempts import finish_attempt, list_attempts, start_attempt
from rollup.continuity import apply_continuity, previous_run_id
from rollup.gmail.session import (
    GmailClient,
    GmailHistoryExpired,
    GmailNotFoundError,
    GmailRawMessage,
    GmailSessionError,
)
from rollup.models import DigestReport, DigestStats
from rollup.output_writers import OutputWriterError, WriteContext
from rollup.render import digest_output_stem
from rollup.rss.cache import (
    count_feeds_needing_fetch,
    fetch_and_cache_feeds,
    items_in_window,
    save_feed_snapshot,
    save_items,
    sweep_old_items,
)
from rollup.rss.config import (
    RssConfig,
    RssFeed,
    filter_rss_feeds,
    folder_name_for_feed,
    list_rss_folder_names,
    parse_rss_config,
    slug_for_url,
)
from rollup.rss.fetch import RssFetchError, fetch_feed
from rollup.rss.models import RssItem
from rollup.rss.parse import parse_feed_xml, parse_item_date
from rollup.state import init_db


def test_parse_rss_config_rejects_bad_tables(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    assert parse_rss_config(None, path=path).enabled is False
    with pytest.raises(ValueError, match="must be a table"):
        parse_rss_config([], path=path)
    with pytest.raises(ValueError, match="unknown key"):
        parse_rss_config({"nope": 1}, path=path)
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        parse_rss_config({"enabled": "yes"}, path=path)
    with pytest.raises(ValueError, match="layout"):
        parse_rss_config({"layout": "river"}, path=path)
    with pytest.raises(ValueError, match="feeds must be a table"):
        parse_rss_config({"feeds": "nope"}, path=path)
    with pytest.raises(ValueError, match="non-empty"):
        parse_rss_config({"feeds": {"": {}}}, path=path)
    with pytest.raises(ValueError, match="must be a table"):
        parse_rss_config({"feeds": {"a": "nope"}}, path=path)
    with pytest.raises(ValueError, match="unknown key"):
        parse_rss_config({"feeds": {"a": {"url": "https://ex.com/f", "extra": 1}}}, path=path)
    with pytest.raises(ValueError, match="url must be a string"):
        parse_rss_config({"feeds": {"a": {"url": ""}}}, path=path)
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        parse_rss_config({"feeds": {"a": {"url": "https://ex.com/f", "enabled": "y"}}}, path=path)
    with pytest.raises(ValueError, match="display_name"):
        parse_rss_config(
            {"feeds": {"a": {"url": "https://ex.com/f", "display_name": 1}}},
            path=path,
        )


def test_parse_rss_config_and_folder_filters(tmp_path: Path) -> None:
    cfg = parse_rss_config(
        {
            "enabled": True,
            "layout": "per_source",
            "feeds": {
                " ex ": {
                    "url": "https://example.com/feed.xml",
                    "display_name": "  Example  ",
                },
                "off": {"url": "https://other.example/atom", "enabled": False},
            },
        },
        path=tmp_path / "c.toml",
    )
    assert cfg.feeds["ex"].display_name == "Example"
    assert slug_for_url("https://example.com/feed.xml").startswith("example-com")
    assert folder_name_for_feed(cfg.feeds["ex"], layout="feed") == "rss:feed"
    assert folder_name_for_feed(cfg.feeds["ex"], layout="per_source").startswith("rss:")
    included = filter_rss_feeds(cfg, folders_include=("rss:feed",), folders_exclude=())
    assert included == ()
    per = filter_rss_feeds(
        cfg,
        folders_include=(folder_name_for_feed(cfg.feeds["ex"], layout="per_source"),),
        folders_exclude=(),
    )
    assert [f.slug for f in per] == ["ex"]
    excluded = filter_rss_feeds(
        RssConfig(enabled=True, layout="feed", feeds=cfg.feeds),
        folders_include=(),
        folders_exclude=("rss:feed",),
    )
    assert excluded == ()
    assert list_rss_folder_names(None) == []
    assert list_rss_folder_names(cfg) == [
        folder_name_for_feed(cfg.feeds["ex"], layout="per_source")
    ]
    assert list_rss_folder_names(RssConfig(enabled=True, layout="feed", feeds={"ex": cfg.feeds["ex"]})) == [
        "rss:feed"
    ]


def test_rss_cache_fresh_stale_and_304(tmp_path: Path, monkeypatch) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    feed = RssFeed(slug="ex", url="https://example.com/feed.xml")
    old = RssItem(
        item_key="rss:item:old",
        feed_url=feed.url,
        title="Old",
        link=None,
        author=None,
        published_at=None,
        body_text="body",
        raw_bytes=4,
    )
    save_items(conn, [old], fetched_at=now - timedelta(days=200))
    assert sweep_old_items(conn, now=now, retention_days=120) == 1
    fresh_item = RssItem(
        item_key="rss:item:new",
        feed_url=feed.url,
        title="New",
        link="https://example.com/a",
        author="Ada",
        published_at=now - timedelta(days=1),
        body_text="hello",
        raw_bytes=5,
    )
    save_items(conn, [fresh_item], fetched_at=now)
    save_feed_snapshot(
        conn,
        feed_url=feed.url,
        etag="W/1",
        last_modified="Mon, 01 Nov 2026 00:00:00 GMT",
        fetched_at=now - timedelta(hours=1),
        item_keys=[fresh_item.item_key],
    )
    conn.commit()
    window_start = now - timedelta(days=7)
    window_end = now
    outside = RssItem(
        item_key="x",
        feed_url=feed.url,
        title="out",
        link=None,
        author=None,
        published_at=now - timedelta(days=30),
        body_text="",
        raw_bytes=0,
    )
    naive = RssItem(
        item_key="n",
        feed_url=feed.url,
        title="naive",
        link=None,
        author=None,
        published_at=datetime(2026, 11, 1),
        body_text="",
        raw_bytes=0,
    )
    kept = items_in_window([fresh_item, outside, naive], window_start=window_start, window_end=window_end)
    assert [i.item_key for i in kept] == ["rss:item:new", "n"]
    cfg = RssConfig(enabled=True, fetch_ttl_hours=24, feeds={"ex": feed})
    by_url, failures, logs = fetch_and_cache_feeds(
        conn,
        (feed,),
        config=cfg,
        refresh=False,
        now=now,
        window_start=window_start,
        window_end=window_end,
    )
    assert failures == []
    assert by_url[feed.url][0].title == "New"
    assert "cache hit" in logs[0]
    assert count_feeds_needing_fetch(conn, (feed,), ttl_hours=24, refresh=True, now=now) == 1

    def not_modified(*_a, **_k):
        return 304, "W/2", None, None

    monkeypatch.setattr("rollup.rss.cache.fetch_feed", not_modified)
    cfg_stale_ttl = RssConfig(enabled=True, fetch_ttl_hours=0, feeds={"ex": feed})
    by_url, failures, logs = fetch_and_cache_feeds(
        conn,
        (feed,),
        config=cfg_stale_ttl,
        refresh=False,
        now=now,
        window_start=window_start,
        window_end=window_end,
    )
    assert failures == []
    assert "RSS fetch" in logs[0]
    assert by_url[feed.url]

    def fail(*_a, **_k):
        raise RssFetchError("rss_fetch_failed", "timeout")

    monkeypatch.setattr("rollup.rss.cache.fetch_feed", fail)
    by_url, failures, _logs = fetch_and_cache_feeds(
        conn,
        (feed,),
        config=cfg_stale_ttl,
        refresh=True,
        now=now,
        window_start=window_start,
        window_end=window_end,
    )
    assert failures[0][0] == feed.url
    assert by_url[feed.url]
    conn.close()


def test_fetch_feed_success_304_and_errors(monkeypatch) -> None:
    def _addr(host, *_a, **_k):
        ip = "169.254.169.254" if host == "metadata.google.internal" else "93.184.216.34"
        return [(None, None, None, None, (ip, 0))]

    monkeypatch.setattr("rollup.webpage.url.socket.getaddrinfo", _addr)

    class Resp:
        def __init__(self, status, body=b"", headers=None, encoding="utf-8"):
            self.status_code = status
            self.headers = headers or {}
            self.encoding = encoding
            self._body = body
            self.is_redirect = status in {301, 302, 303, 307, 308}

        def iter_content(self, chunk_size=65536):
            if not self._body:
                return
            yield self._body

        def close(self):
            return None

    atom = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Atom title</title>
        <link rel="alternate" href="https://example.com/post"/>
        <id>urn:1</id>
        <updated>2026-11-01T00:00:00Z</updated>
        <author><name>Ada</name></author>
        <content>Hello atom</content>
      </entry>
    </feed>"""
    session = MagicMock()
    session.get.return_value = Resp(200, atom, {"ETag": "abc"})
    status, etag, _mod, items = fetch_feed("https://example.com/atom", session=session)
    assert status == 200 and etag == "abc" and items and items[0].author == "Ada"

    session.get.return_value = Resp(304, headers={"ETag": "abc"})
    status, etag, modified, items = fetch_feed(
        "https://example.com/atom",
        etag='"abc"',
        last_modified="Mon, 01 Nov 2026 00:00:00 GMT",
        session=session,
    )
    assert status == 304 and items is None and modified

    session.get.return_value = Resp(500)
    with pytest.raises(RssFetchError, match="HTTP 500"):
        fetch_feed("https://example.com/atom", session=session)

    session.get.return_value = Resp(200, b"x" * 20)
    monkeypatch.setattr("rollup.rss.fetch.MAX_RESPONSE_BYTES", 8)
    with pytest.raises(RssFetchError) as exc:
        fetch_feed("https://example.com/atom", session=session)
    assert exc.value.code == "rss_too_large"

    session.get.side_effect = __import__("requests").RequestException("boom")
    with pytest.raises(RssFetchError, match="boom"):
        fetch_feed("https://example.com/atom", session=session)

    with pytest.raises(RssFetchError) as exc:
        fetch_feed("https://metadata.google.internal/feed", session=session)
    assert exc.value.code == "rss_url_ssrf"


def test_parse_atom_and_dates() -> None:
    assert parse_item_date(None) is None
    assert parse_item_date("not a date") is None
    assert parse_item_date("2026-11-01T00:00:00Z") is not None
    xml = """<rss><channel><item>
      <title> </title><pubDate>not-a-date</pubDate><author>Ada</author>
      <description>plain</description>
    </item></channel></rss>"""
    items = parse_feed_xml(xml, feed_url="https://example.com/f")
    assert items[0].title == "(no title)"
    assert items[0].published_at is None
    assert items[0].author == "Ada"


def test_kindle_config_writer_and_lazy_exports(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "c.toml"
    with pytest.raises(ValueError, match="must be a table"):
        parse_kindle_config([], path=path)
    with pytest.raises(ValueError, match="unknown"):
        parse_kindle_config({"secret": "x"}, path=path)
    with pytest.raises(ValueError, match="to must be a string"):
        parse_kindle_config({"to": 1}, path=path)
    with pytest.raises(ValueError, match="from must be a string"):
        parse_kindle_config({"from": 1}, path=path)
    with pytest.raises(ValueError, match="email"):
        parse_kindle_config({"to": "not-an-email"}, path=path)
    assert parse_kindle_config({"to": " a@kindle.com ", "from": " b@ex.com "}, path=path).sender == "b@ex.com"

    import rollup.addons.kindle as kindle_pkg

    assert kindle_pkg.KindleConfig is KindleConfig
    assert kindle_pkg.KindleOutputWriter is KindleOutputWriter
    with pytest.raises(AttributeError):
        kindle_pkg.__getattr__("missing")

    writer = KindleOutputWriter()
    when = datetime(2026, 11, 2, tzinfo=timezone.utc)
    out = tmp_path / "out"
    out.mkdir()
    stem = digest_output_stem(when, run_id_short="abc")
    html = out / f"{stem}.html"
    html.write_text("<p>hi</p>", encoding="utf-8")
    from rollup.config import Config

    mail = tmp_path / "mail"
    mail.mkdir()
    config = Config(
        root=mail,
        mail_root=tmp_path,
        output_dir=out,
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=100,
        max_chars_for_llm=100,
        max_display_links=4,
        ollama_url="http://127.0.0.1:9",
        ollama_model="x",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
        kindle=KindleConfig(to="a@kindle.com", sender="b@ex.com"),
    )
    report = DigestReport(
        generated_at=when,
        lookback_days=7,
        window_start=when,
        window_end=when,
        dated_by_folder={},
        undated=(),
        stats=DigestStats(
            folders_scanned=0,
            messages_parsed=0,
            dated_included=0,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=0,
            summaries_errors=0,
        ),
    )
    ctx = WriteContext(
        output_dir=out,
        generated_at=when,
        max_display_links=4,
        dry_run=True,
        explicit_outputs=True,
        config=config,
        run_id_short="abc",
    )
    assert writer.write(report, ctx) == []
    args = MagicMock(output=[], xteink=False, x3=False, kindle_to=" c@kindle.com ")
    assert writer.enabled(args, config) is True
    ctx = WriteContext(
        output_dir=out,
        generated_at=when,
        max_display_links=4,
        dry_run=False,
        explicit_outputs=False,
        config=Config(
            root=mail,
            mail_root=tmp_path,
            output_dir=out,
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "logs",
            lookback_days=7,
            folders_include=(),
            folders_exclude=(),
            no_ollama=True,
            include_seen_undated=False,
            rebuild_summaries=False,
            max_body_chars=100,
            max_chars_for_llm=100,
            max_display_links=4,
            ollama_url="http://127.0.0.1:9",
            ollama_model="x",
            allow_remote_ollama=False,
            summary_profile=None,
            summary_variants=(),
            summary_type_routing=None,
            summary_profile_set_path=None,
            export_summary_profile_set_path=None,
            list_summary_profiles=False,
            list_newsletter_types=False,
            summary_routing_report=False,
        ),
    )
    assert writer.write(report, ctx) == []

    monkeypatch.delenv("ROLLUP_KINDLE_SMTP_HOST", raising=False)
    monkeypatch.delenv("ROLLUP_KINDLE_SMTP_PASSWORD", raising=False)
    ctx = WriteContext(
        output_dir=out,
        generated_at=when,
        max_display_links=4,
        dry_run=False,
        explicit_outputs=True,
        config=config,
        run_id_short="abc",
    )
    with pytest.raises(OutputWriterError, match="SMTP_HOST"):
        writer.write(report, ctx)
    monkeypatch.setenv("ROLLUP_KINDLE_SMTP_HOST", "smtp.example")
    monkeypatch.setenv("ROLLUP_KINDLE_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("ROLLUP_KINDLE_SMTP_USER", "user")
    monkeypatch.setenv("ROLLUP_KINDLE_SMTP_PORT", "nope")
    with pytest.raises(OutputWriterError, match="integer"):
        writer.write(report, ctx)
    monkeypatch.setenv("ROLLUP_KINDLE_SMTP_PORT", "587")
    (out / f"{stem}.html").unlink()
    with pytest.raises(OutputWriterError, match="no EPUB or HTML"):
        writer.write(report, ctx)
    html.write_bytes(b"x")
    monkeypatch.setattr("rollup.addons.kindle.writer.MAX_ATTACHMENT_BYTES", 0)
    with pytest.raises(OutputWriterError, match="exceeds"):
        writer.write(report, ctx)
    monkeypatch.setattr("rollup.addons.kindle.writer.MAX_ATTACHMENT_BYTES", 25 * 1024 * 1024)

    class SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            return None

        def login(self, user, password):
            assert user == "user"

        def send_message(self, message):
            assert message["To"] == "a@kindle.com"

    monkeypatch.setattr("smtplib.SMTP", SMTP)
    assert writer.write(report, ctx) == [html]


def test_attempts_and_continuity_edges(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    when = datetime(2026, 11, 2, tzinfo=timezone.utc)
    start_attempt(conn, run_id="r1", started_at=when)
    finish_attempt(conn, run_id="r1", status="weird", finished_at=when, error_summary="x" * 300)
    rows = list_attempts(conn)
    assert rows[0]["status"] == "failure"
    assert len(rows[0]["error_summary"]) == 240
    bare = sqlite3.connect(":memory:")
    assert list_attempts(bare) == []
    assert previous_run_id([]) is None
    dated, undated, failed = apply_continuity((), (), conn=bare)
    assert dated == () and undated == () and failed is True
    conn.close()


def test_gmail_history_pagination_and_batch_skip(tmp_path: Path) -> None:
    client = GmailClient(tmp_path / "token.json")
    pages = [
        {
            "historyId": "2",
            "history": [{"messagesAdded": []}, "skip-me"],
            "nextPageToken": "next",
        },
        {"historyId": "3", "history": []},
    ]

    def _get(path, *, params=None, label=""):
        assert path == "history"
        assert params["startHistoryId"] == "1"
        return pages.pop(0)

    client._get = _get  # type: ignore[method-assign]
    latest, records = client.list_history(start_history_id="1", label_id="L")
    assert latest == "3"
    assert len(records) == 1

    def missing(path, *, params=None, label=""):
        raise GmailNotFoundError("gone")

    client._get = missing  # type: ignore[method-assign]
    with pytest.raises(GmailHistoryExpired):
        client.list_history(start_history_id="1", label_id="L")

    def bad(path, *, params=None, label=""):
        return ["nope"]

    client._get = bad  # type: ignore[method-assign]
    with pytest.raises(GmailSessionError, match="unexpected"):
        client.list_history(start_history_id="1", label_id="L")

    assert client.get_messages_raw([]) == ([], [])
    raw = GmailRawMessage(
        gmail_id="ok",
        thread_id="t",
        label_ids=(),
        internal_date=None,
        raw=b"x",
        size_estimate=1,
    )

    def get_message_raw(gmail_id: str) -> GmailRawMessage:
        if gmail_id == "bad":
            raise GmailSessionError("nope")
        return raw

    client.get_message_raw = get_message_raw  # type: ignore[method-assign]
    found, skipped = client.get_messages_raw(["ok", "bad"])
    assert [m.gmail_id for m in found] == ["ok"]
    assert skipped == ["bad"]


def test_rss_page_saves_and_rejects_bad_url(tmp_path: Path) -> None:
    pytest.importorskip("flask")
    from rollup.web.app import create_app

    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    init_db(state / "rollup.db").close()
    cfg = tmp_path / "config.toml"
    cfg.write_text("lookback_days = 7\n", encoding="utf-8")
    app = create_app(state_dir=state, output_dir=out, mail_root=tmp_path, testing=True, config_path=cfg)
    client = app.test_client()
    page = client.get("/rss")
    assert page.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
    assert token
    bad = client.post(
        "/rss/save",
        data={"csrf_token": token.group(1), "rss_add_url": "http://127.0.0.1/feed"},
    )
    assert bad.status_code in {302, 400}
    page = client.get("/rss")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
    assert token
    ok = client.post(
        "/rss/save",
        data={
            "csrf_token": token.group(1),
            "rss_enabled": "1",
            "rss_layout": "feed",
            "rss_fetch_ttl_hours": "12",
            "rss_add_url": "https://example.com/feed.xml",
        },
        follow_redirects=True,
    )
    assert ok.status_code == 200
    assert "example.com/feed.xml" in ok.get_data(as_text=True)
    assert "enabled = true" in cfg.read_text(encoding="utf-8")
