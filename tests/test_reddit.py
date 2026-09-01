"""Tests for Reddit digest integration (no live network)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rollup.grouping import apply_grouping
from rollup.models import ClassifiedMessage, DigestEntry
from rollup.reddit.config import (
    RedditConfig,
    RedditSub,
    folder_name_for_sub,
    normalize_sub_name,
    parse_reddit_config,
)
from rollup.reddit.fetch import (
    FixtureRedditClient,
    fetch_posts_for_subs,
    posts_from_rss,
)
from rollup.reddit.parse import reddit_post_to_parsed_message, reddit_source_key
from rollup.reddit.models import RedditPost
from rollup.reddit.session import build_rss_url, rss_sort_path
from rollup.run_options import GroupingConfig
from rollup.pipeline import (
    DiscoveryResult,
    ParseCounts,
    ParseResult,
    evaluate_no_input,
    merge_reddit_parse,
    stage_parse_reddit,
)
from rollup.state import SCHEMA_VERSION, get_schema_version, init_db

FIXTURES = Path(__file__).parent / "fixtures" / "reddit"


def _entry(post: RedditPost, *, layout: str = "feed") -> DigestEntry:
    parsed = reddit_post_to_parsed_message(
        post, layout=layout, max_body_chars=50_000
    )
    return DigestEntry(
        classified=ClassifiedMessage(
            parsed=parsed,
            newsletter_type="short_update",
            classification_scores=(),
        ),
        summary=None,
        summary_source="none",
    )


def test_parse_reddit_config() -> None:
    raw = {
        "enabled": True,
        "layout": "feed",
        "sort": "hot",
        "limit": 10,
        "mode": "summary",
        "subs": {
            "python": {"enabled": True},
            "machinelearning": {"enabled": True, "mode": "posts", "limit": 5},
        },
    }
    cfg = parse_reddit_config(raw, path=Path("t.toml"))
    assert cfg.enabled is True
    assert cfg.subs["python"].enabled is True
    assert cfg.subs["machinelearning"].mode == "posts"
    assert cfg.subs["machinelearning"].resolved_limit(10) == 5


def test_normalize_sub_name() -> None:
    assert normalize_sub_name("Python") == "python"
    assert normalize_sub_name("r/machinelearning") == "machinelearning"
    assert normalize_sub_name("") is None
    assert normalize_sub_name("bad/name") is None


def test_folder_name_feed_vs_per_source() -> None:
    assert folder_name_for_sub("python", layout="feed") == "reddit:feed"
    assert folder_name_for_sub("python", layout="per_source") == "reddit:python"


def test_rss_sort_mapping() -> None:
    assert rss_sort_path("hot") == "hot"
    assert rss_sort_path("rising") == "hot"
    assert build_rss_url("python", "top", time_filter="week").endswith("t=week")


def test_fixture_rss_parse() -> None:
    xml = FIXTURES.joinpath("hot.rss").read_text(encoding="utf-8")
    posts = posts_from_rss(xml, subreddit="python")
    assert len(posts) == 2
    assert posts[0].title.startswith("What's new")
    assert posts[0].post_id == "abc123"
    assert posts[0].author == "guido_fan"
    assert posts[0].selftext


def test_fixture_fetch_posts_for_subs() -> None:
    client = FixtureRedditClient(FIXTURES)
    cfg = RedditConfig(enabled=True, sort="hot", limit=10)
    subs = (RedditSub(name="python", enabled=True),)
    result = fetch_posts_for_subs(
        subs,
        config=cfg,
        lookback_days=3650,
        client=client,
    )
    assert len(result["python"]) == 2


def test_rss_window_filter() -> None:
    client = FixtureRedditClient(FIXTURES)
    cfg = RedditConfig(enabled=True, sort="hot", limit=10)
    subs = (RedditSub(name="python", enabled=True),)
    window_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    window_end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    result = fetch_posts_for_subs(
        subs,
        config=cfg,
        lookback_days=7,
        client=client,
        window_start=window_start,
        window_end=window_end,
    )
    assert len(result["python"]) == 1
    assert result["python"][0].post_id == "abc123"


def test_reddit_source_key() -> None:
    assert reddit_source_key("Python") == "reddit:sub:python"


def test_grouping_summary_mode_feed() -> None:
    post = RedditPost(
        post_id="abc",
        subreddit="python",
        title="Hello",
        selftext="body",
        author="user",
        permalink="https://reddit.com/r/python/comments/abc",
        url="https://reddit.com/r/python/comments/abc",
        score=10,
        num_comments=1,
        created_at=datetime.now(timezone.utc),
    )
    cfg = RedditConfig(
        enabled=True,
        mode="summary",
        subs={"python": RedditSub(name="python", enabled=True)},
    )
    result = apply_grouping(
        (_entry(post),),
        (),
        GroupingConfig(enabled=False),
        reddit_config=cfg,
    )
    assert len(result.groups) == 1
    assert result.groups[0].group_type == "subreddit_digest"
    assert result.groups[0].group_summary


def test_grouping_posts_mode() -> None:
    post = RedditPost(
        post_id="abc",
        subreddit="python",
        title="Hello",
        selftext="body",
        author="user",
        permalink="https://reddit.com/r/python/comments/abc",
        url="https://reddit.com/r/python/comments/abc",
        score=10,
        num_comments=1,
        created_at=datetime.now(timezone.utc),
    )
    cfg = RedditConfig(
        enabled=True,
        mode="posts",
        subs={"python": RedditSub(name="python", enabled=True)},
    )
    result = apply_grouping(
        (_entry(post),),
        (),
        GroupingConfig(enabled=False),
        reddit_config=cfg,
    )
    assert result.groups == ()
    assert len(result.dated_items) == 1


def test_stage_parse_reddit_dry_run() -> None:
    cfg = RedditConfig(
        enabled=True,
        subs={"python": RedditSub(name="python", enabled=True)},
    )
    from rollup.config import Config

    config = Config(
        root=Path("."),
        mail_root=Path("."),
        output_dir=Path("/tmp/out"),
        state_dir=Path("/tmp/state"),
        log_dir=Path("/tmp/logs"),
        lookback_days=7,
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
        reddit=cfg,
    )
    msgs, warnings, degraded = stage_parse_reddit(
        config,
        (RedditSub(name="python", enabled=True),),
        dry_run=True,
    )
    assert msgs == []
    assert any(w.code == "reddit_dry_run" for w in warnings)
    assert degraded is False


def test_stage_parse_reddit_fixture_client() -> None:
    cfg = RedditConfig(
        enabled=True,
        subs={"python": RedditSub(name="python", enabled=True)},
    )
    from rollup.config import Config

    config = Config(
        root=Path("."),
        mail_root=Path("."),
        output_dir=Path("/tmp/out"),
        state_dir=Path("/tmp/state"),
        log_dir=Path("/tmp/logs"),
        lookback_days=3650,
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
        reddit=cfg,
    )
    client = FixtureRedditClient(FIXTURES)
    msgs, warnings, degraded = stage_parse_reddit(
        config,
        (RedditSub(name="python", enabled=True),),
        dry_run=False,
        client=client,
        generated_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    assert len(msgs) == 2
    assert not any(w.code == "reddit_fetch_failed" for w in warnings)
    assert degraded is False


def test_merge_reddit_parse() -> None:
    base = ParseResult(
        messages=(),
        counts=ParseCounts(messages_seen=0, messages_parsed=0),
    )
    post = RedditPost(
        post_id="x",
        subreddit="python",
        title="t",
        selftext="b",
        author="u",
        permalink="https://reddit.com/x",
        url="https://reddit.com/x",
        score=1,
        num_comments=0,
        created_at=None,
    )
    msg = reddit_post_to_parsed_message(post, layout="feed", max_body_chars=1000)
    merged = merge_reddit_parse(base, [msg], [])
    assert len(merged.messages) == 1


def test_evaluate_no_input_reddit_only_failure() -> None:
    from rollup.pipeline import StageWarning

    discovery = DiscoveryResult(
        folders=(), reddit_subs=(RedditSub(name="python", enabled=True),)
    )
    parse = ParseResult(
        messages=(),
        counts=ParseCounts(messages_seen=0, messages_parsed=0),
        warnings=(StageWarning(code="reddit_fetch_failed", message="fail"),),
    )
    reason = evaluate_no_input(folders_include=(), discovery=discovery, parse=parse)
    assert reason is not None
    assert "Reddit" in reason


def test_schema_v14_reddit_catalog(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION == 14
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='reddit_sub_catalog'"
    ).fetchone()
    assert row is not None
    conn.close()
