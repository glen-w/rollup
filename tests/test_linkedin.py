"""Tests for LinkedIn content-search ingestion (no live network)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.config import Config, DEFAULT_LOOKBACK_DAYS
from rollup.error_sanitize import sanitize_provider_message
from rollup.linkedin.config import (
    LinkedInConfig,
    LinkedInSearch,
    filter_linkedin_searches,
    folder_name_for_search,
    parse_linkedin_config,
)
from rollup.linkedin.fetch import (
    FixtureLinkedInClient,
    _posts_from_commentary_regexes,
    _posts_from_embedded_json,
    posts_from_fixture_payload,
)
from rollup.linkedin.parse import linkedin_post_to_parsed_message
from rollup.linkedin.url import (
    apply_lookback_to_url,
    from_member_ids,
    lookback_to_date_posted,
    validate_content_search_url,
)
from rollup.linkedin.voyager import posts_from_profile_updates_payload
from rollup.linkedin.models import LinkedInPost
from rollup.pipeline import (
    DiscoveryResult,
    ParseCounts,
    ParseResult,
    evaluate_no_input,
    merge_linkedin_parse,
    stage_discover,
    stage_parse_linkedin,
)
from rollup.user_config import load_toml_file, parse_toml_dict
from rollup.web_ids import validate_message_key, validate_source_key

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"
WATCHLIST_URL = (FIXTURES / "watchlist_url.txt").read_text(encoding="utf-8").strip()


def test_validate_content_search_url_accepts_watchlist() -> None:
    assert validate_content_search_url(WATCHLIST_URL) == WATCHLIST_URL


def test_validate_content_search_url_rejects_jobs() -> None:
    with pytest.raises(ValueError, match="content search"):
        validate_content_search_url("https://www.linkedin.com/jobs/search/")


def test_lookback_to_date_posted_mapping() -> None:
    assert lookback_to_date_posted(1) == "past-24h"
    assert lookback_to_date_posted(7) == "past-week"
    assert lookback_to_date_posted(14) == "past-month"
    assert lookback_to_date_posted(90) == "past-year"


def test_apply_lookback_to_url_replaces_date_posted() -> None:
    out = apply_lookback_to_url(WATCHLIST_URL, 30)
    assert "past-month" in out


def test_from_member_ids_from_watchlist_url() -> None:
    ids = from_member_ids(WATCHLIST_URL)
    assert len(ids) == 12
    assert ids[0].startswith("ACo")
    assert "ACoAAAMN5aEBk7L5BGyjHbFsDr40zYqwuSB7tlw" in ids


def test_posts_from_profile_updates_fixture() -> None:
    import json

    payload = json.loads(
        (FIXTURES / "profile_updates.json").read_text(encoding="utf-8")
    )
    posts = posts_from_profile_updates_payload(payload)
    assert len(posts) == 2
    assert posts[0].activity_id == "7123456789012345678"
    assert posts[0].author_name == "Jane Doe"
    assert posts[0].author_member_id.startswith("ACo")
    assert "feature" in posts[0].text.lower()


def test_created_at_from_activity_snowflake() -> None:
    from datetime import timezone

    from rollup.linkedin.voyager import created_at_from_activity_id

    dt = created_at_from_activity_id("7497268460025184256")
    assert dt is not None
    assert dt.tzinfo is timezone.utc
    assert dt.year == 2026
    assert dt.month == 8
    assert dt.day == 23
    assert created_at_from_activity_id("not-a-number") is None
    assert created_at_from_activity_id(None) is None


def test_posts_from_normalized_restli_star_elements() -> None:
    primary_urn = (
        "urn:li:fs_updateV2:(urn:li:activity:111,MEMBER_SHARES,DEBUG_REASON,DEFAULT,false)"
    )
    nested_urn = (
        "urn:li:fs_updateV2:(urn:li:activity:222,MEMBER_SHARES,EMPTY,RESHARED,false)"
    )
    payload = {
        "data": {"*elements": [primary_urn]},
        "included": [
            {
                "entityUrn": primary_urn,
                "actor": {
                    "name": {"text": "Primary Author"},
                    "urn": "urn:li:fsd_profile:ACoAAAPrimary",
                },
                "commentary": {"text": {"text": "Primary feed item."}},
                "updateMetadata": {"urn": "urn:li:activity:111"},
                "createdAt": 1756387200000,
            },
            {
                "entityUrn": nested_urn,
                "actor": {
                    "name": {"text": "Nested Reshare"},
                    "urn": "urn:li:fsd_profile:ACoAAANested",
                },
                "commentary": {"text": {"text": "Should not be a primary item."}},
                "updateMetadata": {"urn": "urn:li:activity:222"},
                "createdAt": 1756300800000,
            },
        ],
    }
    posts = posts_from_profile_updates_payload(payload)
    assert len(posts) == 1
    assert posts[0].author_name == "Primary Author"
    assert "Primary feed" in posts[0].text


def test_parse_linkedin_toml() -> None:
    data = {
        "linkedin": {
            "enabled": True,
            "searches": {
                "watchlist": {
                    "url": WATCHLIST_URL,
                    "display_name": "LinkedIn watchlist",
                    "enabled": True,
                }
            },
        }
    }
    loaded = parse_toml_dict(data, path=Path("test.toml"))
    assert loaded.linkedin.enabled is True
    assert "watchlist" in loaded.linkedin.searches
    assert loaded.linkedin.searches["watchlist"].url == WATCHLIST_URL


def test_folder_name_and_filter() -> None:
    assert folder_name_for_search("watchlist") == "linkedin:watchlist"
    searches = {
        "watchlist": LinkedInSearch(slug="watchlist", url=WATCHLIST_URL),
        "other": LinkedInSearch(slug="other", url=WATCHLIST_URL, enabled=False),
    }
    all_enabled = filter_linkedin_searches(
        searches, folders_include=(), folders_exclude=()
    )
    assert len(all_enabled) == 1
    included = filter_linkedin_searches(
        searches,
        folders_include=("linkedin:watchlist",),
        folders_exclude=(),
    )
    assert len(included) == 1
    excluded = filter_linkedin_searches(
        searches,
        folders_include=(),
        folders_exclude=("linkedin:watchlist",),
    )
    assert len(excluded) == 0


def test_fixture_to_parsed_message() -> None:
    client = FixtureLinkedInClient(FIXTURES / "watchlist_posts.json")
    search = LinkedInSearch(slug="watchlist", url=WATCHLIST_URL)
    posts = client.fetch_search(search, lookback_days=7)
    assert len(posts) == 2
    msg = linkedin_post_to_parsed_message(
        posts[0], search_slug="watchlist", max_body_chars=50_000
    )
    assert msg.folder_name == "linkedin:watchlist"
    assert msg.message_key.startswith("li:activity:")
    assert msg.source_key.startswith("li:member:")
    validate_message_key(msg.message_key)
    validate_source_key(msg.source_key)


def test_posts_from_nested_commentary_html() -> None:
    html = (
        '{"actor":{"name":{"text":"Jane Doe"},'
        '"urn":"urn:li:member:ACoAAAMN5aEBk7L5BGyjHbFsDr40zYqwuSB7tlw"},'
        '"commentary":{"text":{"text":"Hello from LinkedIn watchlist"}},'
        '"entityUrn":"urn:li:activity:7123456789012345678",'
        '"createdAt":1756387200000}'
    )
    posts = _posts_from_embedded_json(html)
    assert len(posts) == 1
    assert posts[0].text == "Hello from LinkedIn watchlist"
    assert posts[0].activity_id == "7123456789012345678"
    assert posts[0].author_name == "Jane Doe"


def test_posts_from_fixture_payload_list() -> None:
    posts = posts_from_fixture_payload(
        [
            {
                "activity_id": "99",
                "author_name": "A",
                "text": "hello",
                "permalink": "https://example.com",
                "created_at": "2026-08-01T00:00:00+00:00",
            }
        ]
    )
    assert len(posts) == 1
    assert posts[0].activity_id == "99"


def test_stage_parse_linkedin_dry_run_no_cookie(monkeypatch) -> None:
    monkeypatch.delenv("ROLLUP_LINKEDIN_LI_AT", raising=False)
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
        no_linkedin=False,
        linkedin=LinkedInConfig(
            enabled=True,
            searches={
                "watchlist": LinkedInSearch(slug="watchlist", url=WATCHLIST_URL)
            },
        ),
    )
    search = LinkedInSearch(slug="watchlist", url=WATCHLIST_URL)
    msgs, warnings, degraded = stage_parse_linkedin(
        config, (search,), dry_run=True
    )
    assert msgs == []
    assert any(w.code == "linkedin_dry_run" for w in warnings)
    assert any(w.code == "linkedin_no_cookie" for w in warnings)
    assert degraded


def test_stage_parse_linkedin_fixture_client() -> None:
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
        no_linkedin=False,
        linkedin=LinkedInConfig(enabled=True),
    )
    search = LinkedInSearch(slug="watchlist", url=WATCHLIST_URL)
    client = FixtureLinkedInClient(FIXTURES / "watchlist_posts.json")
    msgs, warnings, degraded = stage_parse_linkedin(
        config, (search,), dry_run=False, client=client
    )
    assert len(msgs) == 2
    assert not degraded
    assert not warnings


def test_evaluate_no_input_linkedin_only_failure() -> None:
    discovery = DiscoveryResult(
        folders=(),
        linkedin_searches=(
            LinkedInSearch(slug="watchlist", url=WATCHLIST_URL),
        ),
    )
    parse = ParseResult(
        messages=(),
        counts=ParseCounts(),
        warnings=(
            __import__("rollup.pipeline", fromlist=["StageWarning"]).StageWarning(
                code="linkedin_fetch_failed",
                message="auth",
                folder="linkedin:watchlist",
            ),
        ),
    )
    reason = evaluate_no_input(
        folders_include=(), discovery=discovery, parse=parse
    )
    assert reason is not None
    assert "LinkedIn fetch failed" in reason


def test_sanitize_redacts_li_at() -> None:
    msg = "Cookie: li_at=AQEDAS1234567890abcdef; Path=/"
    assert "[REDACTED]" in sanitize_provider_message(msg)


def test_merge_linkedin_parse() -> None:
    mbox = ParseResult(
        messages=(),
        counts=ParseCounts(messages_seen=0, messages_parsed=0),
    )
    post = LinkedInPost(
        activity_id="1",
        author_name="A",
        author_member_id="ACoAAA",
        text="hi",
        permalink="https://www.linkedin.com/feed/update/urn:li:activity:1",
        created_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    li_msg = linkedin_post_to_parsed_message(
        post, search_slug="watchlist", max_body_chars=10_000
    )
    merged = merge_linkedin_parse(mbox, [li_msg], [])
    assert len(merged.messages) == 1
    assert merged.counts.messages_parsed == 1
