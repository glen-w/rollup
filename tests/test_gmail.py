"""Gmail ingest transport: config, cache, parse equivalence, pipeline (no live network)."""

from __future__ import annotations

import base64
import json
import mailbox
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from rollup.config import Config
from rollup.gmail.auth import PendingAuth, parse_redirect
from rollup.gmail.cache import (
    cached_message_ids,
    fetch_and_cache_gmail_targets,
    get_label_snapshot,
    known_labels,
    load_raw_messages,
    partition_gmail_targets,
    save_label_snapshot,
    save_raw_message,
    sweep_old_messages,
)
from rollup.gmail.config import (
    GmailConfig,
    GmailTarget,
    expand_prefix_targets,
    folder_name_for_label,
    list_gmail_folder_names,
    parse_gmail_config,
    targets_from_config,
)
from rollup.gmail.parse import raw_to_parsed_message
from rollup.gmail.session import (
    GmailAuthError,
    GmailRawMessage,
    GmailSessionError,
    token_path_for,
    token_scope_is_readonly,
    write_token_file,
)
from rollup.parse import parse_mbox_folder
from rollup.pipeline import DiscoveryResult, stage_discover, stage_parse_gmail
from rollup.pipeline.status import evaluate_no_input
from rollup.pipeline.types import ParseCounts, ParseResult, StageWarning
from rollup.state import SCHEMA_VERSION, get_schema_version, init_db

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"


# -- helpers -------------------------------------------------------------------


class FakeGmailClient:
    """In-memory stand-in for GmailClient; records every call."""

    def __init__(
        self,
        labels: dict[str, str],
        messages: dict[str, list[GmailRawMessage]],
        *,
        fail_labels: set[str] | None = None,
        fail_list_labels: bool = False,
    ) -> None:
        self.labels = labels
        self.messages = messages  # label_id -> messages
        self.fail_labels = fail_labels or set()
        self.fail_list_labels = fail_list_labels
        self.calls: list[tuple] = []

    def list_labels(self) -> dict[str, str]:
        self.calls.append(("labels",))
        if self.fail_list_labels:
            raise GmailSessionError("labels.list boom")
        return dict(self.labels)

    def list_message_ids(self, *, label_id: str, query: str | None, max_results: int) -> list[str]:
        self.calls.append(("list", label_id, query, max_results))
        if label_id in self.fail_labels:
            raise GmailSessionError(f"list failed for {label_id}")
        return [m.gmail_id for m in self.messages.get(label_id, [])][:max_results]

    def get_message_raw(self, gmail_id: str) -> GmailRawMessage:
        self.calls.append(("get", gmail_id))
        for msgs in self.messages.values():
            for m in msgs:
                if m.gmail_id == gmail_id:
                    return m
        raise GmailSessionError(f"missing {gmail_id}")


def _raw(gmail_id: str, subject: str, *, days_ago: int = 1, body: str = "Hello world body text.") -> GmailRawMessage:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    date_hdr = when.strftime("%a, %d %b %Y %H:%M:%S +0000")
    text = (
        f"From: Sender <sender@example.com>\r\n"
        f"To: me@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date_hdr}\r\n"
        f"Message-ID: <{gmail_id}@example.com>\r\n"
        f"List-ID: Example Newsletter <news.example.com>\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
    )
    return GmailRawMessage(
        gmail_id=gmail_id,
        thread_id=f"t-{gmail_id}",
        label_ids=("Label_1",),
        internal_date=when,
        raw=text.encode("utf-8"),
        size_estimate=len(text),
    )


def _config(tmp_path: Path, gmail: GmailConfig, **overrides) -> Config:
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
        no_reddit=True,
        no_webpage=True,
        no_gmail=False,
        gmail=gmail,
    )
    base.update(overrides)
    return Config(**base)


# -- config ---------------------------------------------------------------------


def test_parse_gmail_config_labels_and_prefix(tmp_path: Path) -> None:
    cfg = parse_gmail_config(
        {
            "enabled": True,
            "labels": ["Newsletters/tech", " Digests/ "],
            "label_prefix": "Newsletters",
            "fetch_ttl_hours": 12,
            "max_messages_per_label": 50,
            "extra_query": "-label:muted",
        },
        path=tmp_path / "config.toml",
    )
    assert cfg.enabled is True
    assert cfg.labels == ("Newsletters/tech", "Digests")
    assert cfg.label_prefix == "Newsletters"
    assert cfg.effective_strip_prefix == "Newsletters"
    assert cfg.fetch_ttl_hours == 12
    assert cfg.max_messages_per_label == 50
    assert cfg.extra_query == "-label:muted"
    assert cfg.configured


def test_parse_gmail_config_rejects_unknown_and_bad_types(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="unknown key"):
        parse_gmail_config({"bogus": 1}, path=path)
    with pytest.raises(ValueError, match="enabled"):
        parse_gmail_config({"enabled": "yes"}, path=path)
    with pytest.raises(ValueError, match="labels"):
        parse_gmail_config({"labels": "tech"}, path=path)
    with pytest.raises(ValueError, match="fetch_ttl_hours"):
        parse_gmail_config({"fetch_ttl_hours": 999}, path=path)
    with pytest.raises(ValueError, match="max_messages_per_label"):
        parse_gmail_config({"max_messages_per_label": 0}, path=path)
    assert parse_gmail_config(None, path=path) == GmailConfig()


def test_folder_name_for_label_strips_prefix() -> None:
    assert folder_name_for_label("Newsletters/tech", strip_prefix="Newsletters") == "tech"
    assert folder_name_for_label("Newsletters/a/b", strip_prefix="Newsletters/") == "a/b"
    assert folder_name_for_label("Newsletters", strip_prefix="Newsletters") == "Newsletters"
    assert folder_name_for_label("Other/x", strip_prefix="Newsletters") == "Other/x"
    assert folder_name_for_label("tech", strip_prefix=None) == "tech"


def test_targets_from_config_explicit_prefix_and_filters() -> None:
    cfg = GmailConfig(enabled=True, labels=("Newsletters/tech", "Newsletters/enviro"), label_prefix="Newsletters")
    # Nothing cached yet: explicit labels + one pending prefix target.
    targets = targets_from_config(cfg, folders_include=(), folders_exclude=())
    names = [(t.label_name, t.folder_name, t.is_prefix) for t in targets]
    assert names == [
        ("Newsletters/tech", "tech", False),
        ("Newsletters/enviro", "enviro", False),
        ("Newsletters", "Newsletters", True),
    ]
    # Cached labels under the prefix replace the pending target.
    known = {"Newsletters/tech": "L1", "Newsletters/enviro": "L2", "Newsletters/hoops": "L3", "Other": "L9"}
    targets = targets_from_config(cfg, folders_include=(), folders_exclude=("enviro",), known_labels=known)
    assert [t.label_name for t in targets] == ["Newsletters/tech", "Newsletters/hoops"]
    assert all(t.label_id for t in targets)
    targets = targets_from_config(cfg, folders_include=("hoops",), folders_exclude=(), known_labels=known)
    assert [t.folder_name for t in targets] == ["hoops"]


def test_expand_prefix_targets_resolves_ids() -> None:
    cfg = GmailConfig(enabled=True, label_prefix="Newsletters")
    pending = (GmailTarget(label_name="Newsletters", folder_name="Newsletters", is_prefix=True),)
    live = {"Newsletters/tech": "L1", "Newsletters/x/y": "L2", "Inbox": "INBOX"}
    out = expand_prefix_targets(pending, config=cfg, live_labels=live, folders_include=(), folders_exclude=())
    assert [(t.label_name, t.folder_name, t.label_id) for t in out] == [
        ("Newsletters/tech", "tech", "L1"),
        ("Newsletters/x/y", "x/y", "L2"),
    ]
    assert list_gmail_folder_names(GmailConfig(enabled=True, labels=("A/b",), strip_prefix="A")) == ["b"]
    assert list_gmail_folder_names(GmailConfig(enabled=False, labels=("A",))) == []


# -- parse equivalence -----------------------------------------------------------


def test_gmail_raw_parses_identically_to_mbox() -> None:
    from rollup.models import MboxFolder

    mbox_path = FIXTURE_ROOT / "tech"
    folder = MboxFolder(folder_name="tech", relative_path="tech", mbox_path=mbox_path, size_bytes=0)
    via_mbox, errors, folder_errors = parse_mbox_folder(folder, 50_000, 8)
    assert not folder_errors and via_mbox
    box = mailbox.mbox(str(mbox_path), create=False)
    try:
        raw_bytes = [box.get_bytes(key) for key in box.keys()]
    finally:
        box.close()
    assert len(raw_bytes) == len(via_mbox) + errors
    for index, expected in enumerate(via_mbox[:5]):
        raw = GmailRawMessage(
            gmail_id=f"g{index}",
            thread_id="t",
            label_ids=(),
            internal_date=None,
            raw=raw_bytes[index],
            size_estimate=len(raw_bytes[index]),
        )
        got = raw_to_parsed_message(
            raw, folder_name="tech", label_name="Newsletters/tech", max_body_chars=50_000, max_display_links=8
        )
        assert got.message_key == expected.message_key
        assert got.content_hash == expected.content_hash
        assert got.source_key == expected.source_key
        assert got.subject == expected.subject
        assert got.folder_name == "tech"
        assert got.relative_folder_path == "gmail/Newsletters/tech"


# -- schema + cache ---------------------------------------------------------------


def test_schema_v18_gmail_tables(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION == 18
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"gmail_messages", "gmail_label_sync"} <= tables
    conn.close()


def test_cache_round_trip_and_retention(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime.now(timezone.utc)
    msg = _raw("m1", "Hi")
    save_raw_message(conn, msg, fetched_at=now - timedelta(days=200))
    save_raw_message(conn, _raw("m2", "Yo"), fetched_at=now)
    conn.commit()
    assert cached_message_ids(conn, ["m1", "m2", "zz"]) == {"m1", "m2"}
    loaded = load_raw_messages(conn, ["m2", "m1"])
    assert [m.gmail_id for m in loaded] == ["m2", "m1"]
    assert loaded[1].raw == msg.raw
    save_label_snapshot(conn, label_name="Newsletters/tech", label_id="L1", message_ids=["m1", "m2"], fetched_at=now)
    snap = get_label_snapshot(conn, "Newsletters/tech")
    assert snap is not None and snap.message_ids == ("m1", "m2") and snap.label_id == "L1"
    assert known_labels(conn) == {"Newsletters/tech": "L1"}
    assert sweep_old_messages(conn, now=now) == 1
    assert cached_message_ids(conn, ["m1", "m2"]) == {"m2"}
    conn.close()


def test_partition_honours_ttl_and_refresh(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime.now(timezone.utc)
    save_raw_message(conn, _raw("m1", "Hi"), fetched_at=now)
    save_label_snapshot(conn, label_name="A", label_id="L1", message_ids=["m1"], fetched_at=now - timedelta(hours=1))
    target = GmailTarget(label_name="A", folder_name="A", label_id="L1")
    cached, to_fetch, logs = partition_gmail_targets(conn, (target,), ttl_hours=6, refresh=False, now=now)
    assert [m.gmail_id for m in cached["A"]] == ["m1"] and to_fetch == [] and logs
    cached, to_fetch, _ = partition_gmail_targets(conn, (target,), ttl_hours=6, refresh=True, now=now)
    assert cached == {} and to_fetch == [target]
    cached, to_fetch, _ = partition_gmail_targets(
        conn, (target,), ttl_hours=6, refresh=False, now=now + timedelta(hours=7)
    )
    assert to_fetch == [target]
    conn.close()


def test_fetch_and_cache_skips_cached_and_oversized(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime.now(timezone.utc)
    big = _raw("big", "Huge", body="x" * (2 * 1024 * 1024 + 10))
    client = FakeGmailClient(
        {"Newsletters/tech": "L1"},
        {"L1": [_raw("a", "A"), _raw("b", "B"), big]},
    )
    save_raw_message(conn, _raw("a", "A"), fetched_at=now)
    conn.commit()
    cfg = GmailConfig(enabled=True, label_prefix="Newsletters")
    targets = (GmailTarget(label_name="Newsletters", folder_name="Newsletters", is_prefix=True),)
    by_label, folder_by_label, failures, resolved = fetch_and_cache_gmail_targets(
        conn,
        targets,
        config=cfg,
        client=client,
        window_start=now - timedelta(days=7),
        fetched_at=now,
        folders_include=(),
        folders_exclude=(),
    )
    assert failures == []
    assert folder_by_label == {"Newsletters/tech": "tech"}
    assert sorted(m.gmail_id for m in by_label["Newsletters/tech"]) == ["a", "b"]
    gets = [c for c in client.calls if c[0] == "get"]
    assert sorted(c[1] for c in gets) == ["b", "big"]  # "a" came from cache
    list_call = next(c for c in client.calls if c[0] == "list")
    assert list_call[2].startswith("after:")
    snap = get_label_snapshot(conn, "Newsletters/tech")
    assert snap is not None and set(snap.message_ids) == {"a", "b"}
    conn.close()


# -- stage ----------------------------------------------------------------------


def test_stage_parse_gmail_dry_run_without_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ROLLUP_GMAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROLLUP_GMAIL_CLIENT_SECRET", raising=False)
    cfg = GmailConfig(enabled=True, labels=("Newsletters/tech",))
    config = _config(tmp_path, cfg)
    targets = targets_from_config(cfg, folders_include=(), folders_exclude=())
    msgs, warnings, degraded = stage_parse_gmail(config, targets, dry_run=True)
    codes = {w.code for w in warnings}
    assert msgs == []
    assert {"gmail_no_credentials", "gmail_no_token", "gmail_dry_run"} <= codes
    assert degraded is True


def test_stage_parse_gmail_fetches_then_serves_cache(tmp_path: Path) -> None:
    cfg = GmailConfig(enabled=True, label_prefix="Newsletters", fetch_ttl_hours=6)
    config = _config(tmp_path, cfg)
    client = FakeGmailClient(
        {"Newsletters/tech": "L1", "Newsletters/enviro": "L2", "INBOX": "INBOX"},
        {"L1": [_raw("a", "Tech A"), _raw("b", "Tech B")], "L2": [_raw("c", "Enviro C")]},
    )
    discovery = stage_discover(config)
    assert [t.is_prefix for t in discovery.gmail_targets] == [True]
    msgs, warnings, degraded = stage_parse_gmail(config, discovery.gmail_targets, dry_run=False, client=client)
    assert degraded is False and not warnings
    assert sorted((m.folder_name, m.subject) for m in msgs) == [
        ("enviro", "Enviro C"),
        ("tech", "Tech A"),
        ("tech", "Tech B"),
    ]
    assert all(m.message_key.startswith("mid:") for m in msgs)

    # Second run: discovery now sees cached labels; fresh TTL → zero API calls.
    discovery2 = stage_discover(config)
    assert sorted(t.label_name for t in discovery2.gmail_targets) == ["Newsletters/enviro", "Newsletters/tech"]
    client.calls.clear()
    msgs2, warnings2, degraded2 = stage_parse_gmail(config, discovery2.gmail_targets, dry_run=False, client=client)
    assert client.calls == []
    assert len(msgs2) == 3 and not warnings2 and degraded2 is False

    # Dry-run reports cached reuse, no network.
    _, dry_warnings, _ = stage_parse_gmail(config, discovery2.gmail_targets, dry_run=True, client=client)
    assert client.calls == []
    assert all(w.code == "gmail_dry_run" for w in dry_warnings)
    assert any("reuse cached" in w.message for w in dry_warnings)


def test_stage_parse_gmail_stale_fallback_and_failure(tmp_path: Path) -> None:
    cfg = GmailConfig(enabled=True, labels=("Newsletters/tech", "Newsletters/none"), fetch_ttl_hours=6)
    config = _config(tmp_path, cfg, gmail_refresh=True)
    good = FakeGmailClient({"Newsletters/tech": "L1", "Newsletters/none": "L2"}, {"L1": [_raw("a", "Tech A")], "L2": []})
    targets = targets_from_config(cfg, folders_include=(), folders_exclude=())
    msgs, warnings, degraded = stage_parse_gmail(config, targets, dry_run=False, client=good)
    assert len(msgs) == 1 and degraded is False

    bad = FakeGmailClient({"Newsletters/tech": "L1", "Newsletters/none": "L2"}, {}, fail_labels={"L1", "L2"})
    msgs, warnings, degraded = stage_parse_gmail(config, targets, dry_run=False, client=bad)
    codes = sorted(w.code for w in warnings)
    assert degraded is True
    assert codes == ["gmail_cache_stale", "gmail_fetch_failed"]
    assert [m.subject for m in msgs] == ["Tech A"]  # served from stale cache

    # labels.list itself failing degrades every target.
    dead = FakeGmailClient({}, {}, fail_list_labels=True)
    msgs, warnings, degraded = stage_parse_gmail(config, targets, dry_run=False, client=dead)
    assert degraded is True and len(warnings) == 2


def test_stage_parse_gmail_unknown_label_is_reported(tmp_path: Path) -> None:
    cfg = GmailConfig(enabled=True, labels=("Nope",))
    config = _config(tmp_path, cfg)
    client = FakeGmailClient({"Other": "L1"}, {})
    targets = targets_from_config(cfg, folders_include=(), folders_exclude=())
    msgs, warnings, degraded = stage_parse_gmail(config, targets, dry_run=False, client=client)
    assert msgs == [] and degraded is True
    assert warnings[0].code == "gmail_fetch_failed" and "not found" in warnings[0].message


def test_evaluate_no_input_counts_gmail_targets() -> None:
    discovery = DiscoveryResult(
        folders=(), gmail_targets=(GmailTarget(label_name="A", folder_name="A"),)
    )
    empty = ParseResult(messages=(), counts=ParseCounts())
    assert evaluate_no_input(folders_include=(), discovery=discovery, parse=empty) is None
    failed = ParseResult(
        messages=(),
        counts=ParseCounts(),
        warnings=(StageWarning(code="gmail_fetch_failed", message="x"),),
    )
    reason = evaluate_no_input(folders_include=(), discovery=discovery, parse=failed)
    assert reason is not None and "Gmail fetch failed" in reason
    assert evaluate_no_input(folders_include=(), discovery=DiscoveryResult(folders=()), parse=empty) is not None


def test_run_digest_gmail_only_without_mbox_root(tmp_path: Path) -> None:
    """A Gmail-only setup has no local mbox tree and still publishes."""
    from rollup.pipeline import run_digest
    from rollup.run_options import GroupingConfig, RunOptions

    cfg = GmailConfig(enabled=True, label_prefix="Newsletters")
    config = _config(tmp_path, cfg, root=tmp_path / "missing" / "Newsletters.sbd", mail_root=tmp_path / "missing")
    for d in (config.output_dir, config.state_dir, config.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    client = FakeGmailClient({"Newsletters/tech": "L1"}, {"L1": [_raw("a", "Gmail only subject")]})
    with patch("rollup.gmail.session.build_gmail_client", return_value=client):
        result = run_digest(
            config,
            RunOptions(dry_run=False, write_manifest=False),
            grouping=GroupingConfig(enabled=False),
        )
    assert result.status == "success", result.error_message
    assert result.md_path is not None
    assert "Gmail only subject" in result.md_path.read_text(encoding="utf-8")
    assert result.aggregated.gmail_degraded is False


def test_digest_without_gmail_flag_never_touches_gmail(tmp_path: Path) -> None:
    cfg = GmailConfig(enabled=True, labels=("Newsletters/tech",))
    config = _config(tmp_path, cfg, no_gmail=True)
    discovery = stage_discover(config)
    assert discovery.gmail_targets == ()


# -- session / auth helpers --------------------------------------------------------


def test_write_token_file_is_private_and_scope_check(tmp_path: Path) -> None:
    path = token_path_for(tmp_path / "state")
    write_token_file(path, {"refresh_token": "r", "scope": "https://www.googleapis.com/auth/gmail.readonly"})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    data = json.loads(path.read_text())
    assert token_scope_is_readonly(data) is True
    assert token_scope_is_readonly({"scope": "https://mail.google.com/"}) is False
    assert token_scope_is_readonly({}) is True


def test_parse_redirect_validates_state() -> None:
    pending = PendingAuth(
        client_id="c", client_secret="s", redirect_uri="http://127.0.0.1:1",
        state="abc", code_verifier="v", auth_url="https://x",
    )
    assert parse_redirect(pending, "http://127.0.0.1:1/?state=abc&code=THECODE") == "THECODE"
    assert parse_redirect(pending, "state=abc&code=C2") == "C2"
    with pytest.raises(GmailAuthError, match="state mismatch"):
        parse_redirect(pending, "http://127.0.0.1:1/?state=zzz&code=THECODE")
    with pytest.raises(GmailAuthError, match="error"):
        parse_redirect(pending, "?error=access_denied&state=abc")


def test_client_refreshes_token_and_lists_labels(tmp_path: Path, monkeypatch) -> None:
    import requests

    from rollup.gmail.session import GmailClient

    monkeypatch.setenv("ROLLUP_GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("ROLLUP_GMAIL_CLIENT_SECRET", "csec")
    token_path = tmp_path / "gmail_token.json"
    write_token_file(token_path, {"refresh_token": "rt"})

    class Resp:
        def __init__(self, status: int, payload: dict, headers: dict | None = None) -> None:
            self.status_code = status
            self._payload = payload
            self.headers = headers or {"Content-Type": "application/json"}
            self.text = json.dumps(payload)

        def json(self) -> dict:
            return self._payload

    class Sess:
        def __init__(self) -> None:
            self.posts: list = []
            self.gets: list = []

        def post(self, url, data=None, headers=None, timeout=None):
            self.posts.append((url, data))
            return Resp(200, {"access_token": "AT", "expires_in": 3600, "scope": "https://www.googleapis.com/auth/gmail.readonly"})

        def get(self, url, headers=None, timeout=None):
            self.gets.append((url, headers))
            if len(self.gets) == 1:
                return Resp(429, {}, {"Retry-After": "0"})
            if url.endswith("/labels"):
                return Resp(200, {"labels": [{"id": "L1", "name": "Newsletters/tech"}, {"id": "INBOX", "name": "INBOX"}]})
            if "format=raw" in url:
                raw = base64.urlsafe_b64encode(b"Subject: hi\r\n\r\nbody").decode().rstrip("=")
                return Resp(200, {"id": "m1", "threadId": "t1", "labelIds": ["L1"], "internalDate": "1700000000000", "raw": raw, "sizeEstimate": 20})
            return Resp(200, {"messages": [{"id": "m1"}], "nextPageToken": None})

    sess = Sess()
    client = GmailClient(token_path, session=sess, sleep=lambda s: None)  # type: ignore[arg-type]
    labels = client.list_labels()
    assert labels == {"Newsletters/tech": "L1", "INBOX": "INBOX"}
    assert len(sess.posts) == 1 and sess.posts[0][1]["grant_type"] == "refresh_token"
    assert sess.gets[1][1]["Authorization"] == "Bearer AT"
    ids = client.list_message_ids(label_id="L1", query="after:2024/01/01", max_results=10)
    assert ids == ["m1"]
    msg = client.get_message_raw("m1")
    assert msg.raw.startswith(b"Subject: hi") and msg.internal_date is not None
    # Token file now carries the cached access token.
    assert json.loads(token_path.read_text())["access_token"] == "AT"
    assert isinstance(requests.Session(), requests.Session)


# -- surfaces ---------------------------------------------------------------------


def test_cli_parser_gmail_flags_and_subcommands() -> None:
    from rollup.cli_parser import build_parser

    parser = build_parser()
    args = parser.parse_args(["digest", "--gmail", "--gmail-refresh", "--no-ollama"])
    assert args.gmail is True and args.gmail_refresh is True
    args = parser.parse_args(["gmail", "status", "--json"])
    assert args.command == "gmail" and args.gmail_command == "status" and args.json is True
    args = parser.parse_args(["gmail", "auth", "--manual", "--no-browser"])
    assert args.manual is True and args.no_browser is True


def test_user_config_and_argv_round_trip(tmp_path: Path) -> None:
    from rollup.config_service import build_digest_argv, resolve_effective
    from rollup.user_config import parse_toml_dict

    loaded = parse_toml_dict(
        {"gmail": {"enabled": True, "label_prefix": "Newsletters", "labels": ["Digests"]}},
        path=tmp_path / "config.toml",
    )
    assert loaded.gmail.enabled and loaded.gmail.label_prefix == "Newsletters"
    effective = resolve_effective(loaded)
    argv = build_digest_argv(effective)
    assert "--gmail" in argv


def test_run_progress_gmail_phase() -> None:
    from rollup.web.run_progress import parse_run_progress

    lines = [
        "Digest: root=/x folders=0 linkedin=0 reddit=0 webpage=0 gmail=2 lookback=7d dry_run=False no_ollama=True",
        "Gmail fetch 1/2 Newsletters/tech",
    ]
    out = parse_run_progress(lines, dry_run=False, status="running")
    assert out["phase"] == "gmail"
    assert "Newsletters/tech" in (out["detail"] or "")


def test_doctor_reports_gmail_readiness(tmp_path: Path, monkeypatch) -> None:
    from rollup.doctor import _check_gmail_auth

    monkeypatch.delenv("ROLLUP_GMAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROLLUP_GMAIL_CLIENT_SECRET", raising=False)
    cfg = GmailConfig(enabled=True, labels=("Newsletters/tech",))
    config = _config(tmp_path, cfg)
    check = _check_gmail_auth(config)
    assert check is not None and check.status == "warn"
    assert "token missing" in check.message
    assert _check_gmail_auth(_config(tmp_path, GmailConfig(enabled=False), no_gmail=True)) is None
    monkeypatch.setenv("ROLLUP_GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("ROLLUP_GMAIL_CLIENT_SECRET", "csec")
    write_token_file(token_path_for(config.state_dir), {"refresh_token": "r", "scope": "https://www.googleapis.com/auth/gmail.readonly"})
    check = _check_gmail_auth(config)
    assert check is not None and check.status == "pass"
