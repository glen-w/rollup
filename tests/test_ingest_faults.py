"""Faults for network ingest: stale cache, sole-source exit, bombs, SSRF, Kindle SMTP."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rollup.config import Config
from rollup.gmail.cache import apply_history_delta
from rollup.gmail.session import GmailHistoryExpired
from rollup.pipeline.ingest import stage_parse_rss
from rollup.pipeline.status import evaluate_no_input, format_partial_reasons
from rollup.pipeline.types import AggregatedResults, DiscoveryResult, ParseCounts, ParseResult, StageWarning
from rollup.rss.config import RssConfig, RssFeed
from rollup.rss.fetch import RssFetchError, fetch_feed
from rollup.rss.parse import RssParseError, parse_feed_xml
from rollup.state import init_db


def _config(tmp_path: Path, **kwargs) -> Config:
    root = tmp_path / "mail"
    root.mkdir()
    base = dict(
        root=root,
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
        max_body_chars=1000,
        max_chars_for_llm=1000,
        max_display_links=8,
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
    )
    base.update(kwargs)
    return Config(**base)


def test_history_delta_label_removed() -> None:
    ids = apply_history_delta(
        ["keep", "drop"],
        [{"labelsRemoved": [{"message": {"id": "drop"}, "labelIds": ["LBL"]}]}],
        label_id="LBL",
        limit=10,
    )
    assert ids == ["keep"]


def test_expired_history_type() -> None:
    assert issubclass(GmailHistoryExpired, Exception)


def test_rss_truncated_xml_does_not_escape_stage(tmp_path: Path, monkeypatch) -> None:
    init_db(tmp_path / "state" / "rollup.db").close()
    feed = RssFeed(slug="ex", url="https://example.com/feed.xml", enabled=True)
    config = _config(
        tmp_path,
        no_rss=False,
        rss=RssConfig(enabled=True, feeds={"ex": feed}),
    )

    def boom(*_a, **_k):
        raise RssFetchError("rss_xml_invalid", "truncated")

    monkeypatch.setattr("rollup.rss.cache.fetch_feed", boom)
    messages, warnings, degraded = stage_parse_rss(
        config, (feed,), dry_run=False, generated_at=datetime.now(timezone.utc)
    )
    assert messages == []
    assert degraded is True
    assert any(w.code == "rss_fetch_failed" for w in warnings)


def test_rss_doctype_and_truncated_parse() -> None:
    with pytest.raises(RssParseError):
        parse_feed_xml("<!DOCTYPE rss [<!ENTITY a 'b'>]><rss></rss>", feed_url="https://example.com/f")
    with pytest.raises(RssParseError):
        parse_feed_xml("<rss><channel><item>", feed_url="https://example.com/f")


def test_rss_redirect_to_metadata_is_ssrf(monkeypatch) -> None:
    class Resp:
        def __init__(self, status, headers, body=b""):
            self.status_code = status
            self.headers = headers
            self._body = body
            self.is_redirect = status in {301, 302, 303, 307, 308}

        def iter_content(self, chunk_size=65536):
            yield self._body

        def close(self):
            return None

    session = MagicMock()
    session.get.side_effect = [
        Resp(302, {"Location": "https://evil.example/secret"}),
    ]
    def _addr(host, *_a, **_k):
        ip = "169.254.169.254" if "evil" in str(host) else "93.184.216.34"
        return [(None, None, None, None, (ip, 0))]

    monkeypatch.setattr("rollup.webpage.url.socket.getaddrinfo", _addr)
    with pytest.raises(RssFetchError) as exc:
        fetch_feed("https://example.com/feed.xml", session=session)
    assert exc.value.code == "rss_url_ssrf"


def test_rss_only_and_mixed_status() -> None:
    discovery = DiscoveryResult(folders=(), rss_feeds=("f",))
    parse = ParseResult(
        messages=(),
        counts=ParseCounts(),
        warnings=(StageWarning(code="rss_fetch_failed", message="timeout"),),
    )
    assert "RSS fetch failed" in (evaluate_no_input(folders_include=(), discovery=discovery, parse=parse) or "")
    agg = AggregatedResults(usable_digest=True, dated_outputs_written=True, rss_degraded=True)
    assert "rss" in format_partial_reasons(agg)
    agg2 = AggregatedResults(
        usable_digest=True, dated_outputs_written=True, continuity_ledger_failed=True
    )
    assert "continuity ledger" in format_partial_reasons(agg2)


def test_rss_dry_run_does_not_fetch(tmp_path: Path, monkeypatch) -> None:
    feed = RssFeed(slug="ex", url="https://example.com/feed.xml")
    config = _config(tmp_path, no_rss=False, rss=RssConfig(enabled=True, feeds={"ex": feed}))
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("network")

    monkeypatch.setattr("rollup.rss.cache.fetch_feed", boom)
    init_db(config.db_path).close()
    _msgs, warnings, degraded = stage_parse_rss(
        config, (feed,), dry_run=True, generated_at=datetime.now(timezone.utc)
    )
    assert called["n"] == 0
    assert degraded is False
    assert warnings[0].code == "rss_dry_run"


def test_kindle_smtp_failure_is_optional_writer_error(tmp_path: Path, monkeypatch) -> None:
    from rollup.addons.kindle.writer import KindleOutputWriter
    from rollup.models import DigestReport, DigestStats
    from rollup.output_writers import OutputWriterError, WriteContext

    html = tmp_path / "out"
    html.mkdir()
    when = datetime(2026, 11, 2, tzinfo=timezone.utc)
    from rollup.render import digest_output_stem

    stem = digest_output_stem(when, run_id_short=None)
    (html / f"{stem}.html").write_text("<p>hi</p>", encoding="utf-8")
    monkeypatch.setenv("ROLLUP_KINDLE_SMTP_HOST", "smtp.example")
    monkeypatch.setenv("ROLLUP_KINDLE_SMTP_PASSWORD", "secret")
    monkeypatch.setattr(
        "smtplib.SMTP",
        lambda *a, **k: (_ for _ in ()).throw(OSError("smtp down")),
    )
    from rollup.addons.kindle.config import KindleConfig

    config = _config(tmp_path, kindle=KindleConfig(to="a@kindle.com", sender="b@example.com"))
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
        output_dir=html,
        generated_at=when,
        max_display_links=8,
        dry_run=False,
        explicit_outputs=True,
        config=config,
    )
    with pytest.raises(OutputWriterError, match="SMTP"):
        KindleOutputWriter().write(report, ctx)
