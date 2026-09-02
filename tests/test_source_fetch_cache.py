"""Tests for Reddit/LinkedIn listing persistence and TTL cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from rollup.config import Config
from rollup.linkedin.cache import (
    get_article_body,
    partition_linkedin_searches,
    save_listing_snapshot,
    store_article_body,
)
from rollup.linkedin.config import LinkedInConfig, LinkedInSearch
from rollup.linkedin.models import LinkedInPost
from rollup.pipeline import stage_parse_reddit
from rollup.reddit.cache import (
    count_subs_needing_fetch,
    partition_reddit_subs,
    save_listing_snapshot as save_reddit_snapshot,
)
from rollup.reddit.config import RedditConfig, RedditSub
from rollup.reddit.models import RedditPost
from rollup.source_fetch_cache import snapshot_is_fresh
from rollup.state import init_db

FIXTURES = Path(__file__).parent / "fixtures" / "reddit"
WATCHLIST_URL = (
    "https://www.linkedin.com/search/results/content/"
    "?origin=FACETED_SEARCH&fromMember=%5B%22ACoAAB%22%5D"
)


def _reddit_config(**kwargs) -> Config:
    reddit_kwargs = {k: v for k, v in kwargs.items() if k in RedditConfig.__dataclass_fields__}
    base_kwargs = {k: v for k, v in kwargs.items() if k not in RedditConfig.__dataclass_fields__}
    return Config(
        root=Path("."),
        mail_root=Path("."),
        output_dir=Path("/tmp/out"),
        state_dir=Path("/tmp/state"),
        log_dir=Path("/tmp/logs"),
        lookback_days=base_kwargs.get("lookback_days", 7),
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=10_000,
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
        no_reddit=False,
        reddit=RedditConfig(enabled=True, fetch_ttl_hours=24, **reddit_kwargs),
    )


def _sample_post(sub: str = "python", post_id: str = "abc123") -> RedditPost:
    return RedditPost(
        post_id=post_id,
        subreddit=sub,
        title="Test title",
        selftext="Body",
        author="user",
        permalink=f"https://reddit.com/r/{sub}/comments/{post_id}/",
        url=f"https://reddit.com/r/{sub}/comments/{post_id}/",
        score=10,
        num_comments=2,
        created_at=datetime.now(timezone.utc),
    )


def test_snapshot_is_fresh_respects_ttl() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    fetched = now - timedelta(hours=3)
    assert snapshot_is_fresh(fetched, 24, now)
    assert not snapshot_is_fresh(fetched, 2, now)
    assert not snapshot_is_fresh(fetched, 0, now)


def test_reddit_cache_hit_skips_network(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now(timezone.utc)
    sub = RedditSub(name="python", enabled=True, limit=1)
    config = RedditConfig(enabled=True, fetch_ttl_hours=24, limit=10)
    posts = [_sample_post()]
    save_reddit_snapshot(
        conn,
        sub=sub,
        config=config,
        lookback_days=7,
        posts=posts,
        fetched_at=now,
    )
    cached, to_fetch, logs = partition_reddit_subs(
        conn,
        (sub,),
        config=config,
        lookback_days=7,
        ttl_hours=24,
        refresh=False,
        now=now,
    )
    assert to_fetch == []
    assert "python" in cached
    assert len(cached["python"]) == 1
    assert logs
    conn.close()


def test_reddit_refresh_and_zero_ttl_refetch(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now(timezone.utc)
    sub = RedditSub(name="python", enabled=True)
    config = RedditConfig(enabled=True, fetch_ttl_hours=24)
    save_reddit_snapshot(
        conn,
        sub=sub,
        config=config,
        lookback_days=7,
        posts=[_sample_post()],
        fetched_at=now,
    )
    _, to_fetch_refresh, _ = partition_reddit_subs(
        conn,
        (sub,),
        config=config,
        lookback_days=7,
        ttl_hours=24,
        refresh=True,
        now=now,
    )
    assert to_fetch_refresh == [sub]
    config_zero = RedditConfig(enabled=True, fetch_ttl_hours=0)
    _, to_fetch_zero, _ = partition_reddit_subs(
        conn,
        (sub,),
        config=config_zero,
        lookback_days=7,
        ttl_hours=0,
        refresh=False,
        now=now,
    )
    assert to_fetch_zero == [sub]
    conn.close()


def test_reddit_limit_increase_refetches(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now(timezone.utc)
    sub = RedditSub(name="python", enabled=True, limit=5)
    config = RedditConfig(enabled=True, fetch_ttl_hours=24, limit=10)
    save_reddit_snapshot(
        conn,
        sub=sub,
        config=config,
        lookback_days=7,
        posts=[_sample_post(post_id=f"id{i}") for i in range(3)],
        fetched_at=now,
    )
    _, to_fetch, _ = partition_reddit_subs(
        conn,
        (sub,),
        config=config,
        lookback_days=7,
        ttl_hours=24,
        refresh=False,
        now=now,
    )
    assert to_fetch == [sub]
    conn.close()


def test_stage_parse_reddit_uses_cache(tmp_path: Path) -> None:
    from dataclasses import replace

    from rollup.reddit.fetch import FixtureRedditClient

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    config = replace(
        _reddit_config(lookback_days=3650, limit=2),
        state_dir=state_dir,
        reddit_refresh=False,
    )
    sub = RedditSub(name="python", enabled=True, limit=2)
    client = FixtureRedditClient(FIXTURES)
    when = datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc)

    msgs1, _, _ = stage_parse_reddit(
        config, (sub,), dry_run=False, client=client, generated_at=when
    )
    assert msgs1

    class _FailClient:
        def fetch_listing(self, *args, **kwargs):
            raise RuntimeError("should not fetch")

    msgs2, warnings, degraded = stage_parse_reddit(
        config,
        (sub,),
        dry_run=False,
        client=_FailClient(),
        generated_at=when,
    )
    assert msgs2
    assert not degraded
    assert not any(w.code == "reddit_fetch_failed" for w in warnings)


def test_linkedin_listing_cache(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now(timezone.utc)
    search = LinkedInSearch(slug="watchlist", url=WATCHLIST_URL)
    post = LinkedInPost(
        activity_id="123",
        author_name="Alice",
        author_member_id="ACoAAB",
        text="Hello",
        permalink="https://linkedin.com/feed/update/123",
        created_at=now,
    )
    save_listing_snapshot(conn, search=search, posts=[post], fetched_at=now)
    cached, to_fetch, logs = partition_linkedin_searches(
        conn,
        (search,),
        ttl_hours=24,
        refresh=False,
        now=now,
    )
    assert to_fetch == []
    assert cached["watchlist"][0].text == "Hello"
    assert logs
    conn.close()


def test_linkedin_article_body_cache(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now(timezone.utc)
    url = "https://example.com/article"
    store_article_body(conn, url, "Full article text", fetched_at=now)
    assert get_article_body(conn, url) == "Full article text"
    conn.close()


def test_parse_reddit_config_fetch_ttl() -> None:
    from rollup.reddit.config import parse_reddit_config

    cfg = parse_reddit_config({"enabled": True, "fetch_ttl_hours": 12}, path=Path("t.toml"))
    assert cfg.fetch_ttl_hours == 12
    with pytest.raises(ValueError):
        parse_reddit_config({"fetch_ttl_hours": 200}, path=Path("t.toml"))


def test_parse_linkedin_config_fetch_ttl() -> None:
    from rollup.linkedin.config import parse_linkedin_config

    cfg = parse_linkedin_config({"fetch_ttl_hours": 6}, path=Path("t.toml"))
    assert cfg.fetch_ttl_hours == 6


def test_count_subs_needing_fetch(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now(timezone.utc)
    subs = (
        RedditSub(name="a", enabled=True, limit=1),
        RedditSub(name="b", enabled=True, limit=1),
    )
    config = RedditConfig(enabled=True, fetch_ttl_hours=24)
    save_reddit_snapshot(
        conn,
        sub=subs[0],
        config=config,
        lookback_days=7,
        posts=[_sample_post(sub="a")],
        fetched_at=now,
    )
    assert (
        count_subs_needing_fetch(
            conn,
            subs,
            config=config,
            lookback_days=7,
            ttl_hours=24,
            refresh=False,
            now=now,
        )
        == 1
    )
    conn.close()
