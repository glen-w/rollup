"""Group-summary hardening: eligibility, budget, cache severity, order."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rollup.config import Config, DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO
from rollup.group_summarize import (
    GROUP_SUMMARY_MAX_OUTPUT_CHARS,
    _is_eligible,
    apply_group_summaries,
)
from rollup.models import (
    ClassifiedMessage,
    DigestEntry,
    DigestGroup,
    ParsedMessage,
)


def _entry(key: str, summary: str = "usable summary text here") -> DigestEntry:
    parsed = ParsedMessage(
        message_key=key,
        content_hash=f"hash-{key}",
        folder_name="tech",
        relative_folder_path="tech",
        subject=f"Subj {key}",
        sender="Sender <s@example.com>",
        date_raw="",
        date_parsed=datetime(2026, 7, 1, tzinfo=timezone.utc),
        body_text="body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=3,
        preview="preview",
        parse_warnings=(),
    )
    return DigestEntry(
        classified=ClassifiedMessage(
            parsed=parsed,
            newsletter_type="short_update",
            classification_scores=(("short_update", 1.0),),
        ),
        summary=summary,
        summary_source="preview_fallback",
    )


def _group(
    gid: str,
    n: int = 3,
    *,
    gtype: str = "notification_stream",
) -> DigestGroup:
    return DigestGroup(
        group_id=gid,
        group_type=gtype,  # type: ignore[arg-type]
        display_name=gid,
        sender_normalized="sender",
        folder_name="tech",
        entries=tuple(_entry(f"{gid}:{i}") for i in range(n)),
    )


def _config(**kwargs) -> Config:
    base = dict(
        root=Path("/tmp"),
        mail_root=Path("/tmp/mail"),
        output_dir=Path("/tmp/out"),
        state_dir=Path("/tmp/state"),
        log_dir=Path("/tmp/logs"),
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        dry_run=False,
        no_ollama=False,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=1000,
        max_chars_for_llm=1000,
        max_display_links=8,
        ollama_url="http://127.0.0.1:11434/api/generate",
        ollama_model="m",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
        verbose=False,
        quiet=True,
        final_review_max_changed_chars_ratio=DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
        min_usable_member_summaries=2,
    )
    base.update(kwargs)
    return Config(**base)


def test_eligibility_type_and_size() -> None:
    g = _group("g1", n=2)
    assert not _is_eligible(g, min_group_size=3, min_usable=2)
    assert _is_eligible(_group("g1", n=3), min_group_size=3, min_usable=2)
    g3 = _group("g1", n=3, gtype="daily_editions")
    assert _is_eligible(g3, min_group_size=3, min_usable=2)
    g_standalone = _group("s", n=3, gtype="standalone")
    assert not _is_eligible(g_standalone, min_group_size=3, min_usable=2)


def test_order_preserved_with_mixed_outcomes() -> None:
    g_a = _group("a", n=3)
    g_b = _group("b", n=3)
    g_c = _group("c", n=3)
    dated = {"tech": (g_a, g_b, g_c)}

    call_count = {"n": 0}

    def fake_call(prompt, config, *, stats):
        call_count["n"] += 1
        if call_count["n"] == 2:
            stats["stream_failures"] += 1
            return None, "ollama_http_error"
        return "A short group blurb about the stream.", None

    with (
        patch("rollup.group_summarize._get_cached", return_value=None),
        patch("rollup.group_summarize._store_cached"),
        patch("rollup.group_summarize._call_ollama_for_group", side_effect=fake_call),
    ):
        new_dated, _, meta = apply_group_summaries(
            dated, (), _config(), None, max_calls=10
        )

    ids = [g.group_id for g in new_dated["tech"] if hasattr(g, "group_id")]
    assert ids == ["a", "b", "c"]
    assert meta.ollama_calls >= 2


def test_cache_hit_does_not_increment_ollama_calls() -> None:
    g = _group("a", n=3)
    with patch(
        "rollup.group_summarize._get_cached", return_value="Cached blurb text."
    ):
        _, _, meta = apply_group_summaries(
            {"tech": (g,)}, (), _config(), MagicMock(), max_calls=8
        )
    assert meta.cache_hits == 1
    assert meta.ollama_calls == 0
    assert meta.groups_succeeded == 1


def test_retry_consumes_call_budget() -> None:
    g = _group("a", n=3)

    def always_fail(prompt, config, *, stats):
        stats["stream_failures"] += 1
        return None, "ollama_http_error"

    with (
        patch("rollup.group_summarize._get_cached", return_value=None),
        patch("rollup.group_summarize._call_ollama_for_group", side_effect=always_fail),
    ):
        _, _, meta = apply_group_summaries(
            {"tech": (g,)}, (), _config(), None, max_calls=8
        )
    # 1 + GROUP_SUMMARY_MAX_RETRIES attempts
    assert meta.ollama_calls >= 2
    assert meta.degraded is True
    assert meta.groups_failed == 1


def test_cache_write_failure_still_returns_summary() -> None:
    g = _group("a", n=3)

    def ok_call(prompt, config, *, stats):
        return "Generated group summary blurb.", None

    def fail_store(conn, cache_key, summary, stats):
        stats["errors"] += 1
        stats["cache_write_errors"] += 1
        stats["error_counts"]["cache_write_error"] += 1
        stats["degraded"] = True

    with (
        patch("rollup.group_summarize._get_cached", return_value=None),
        patch("rollup.group_summarize._call_ollama_for_group", side_effect=ok_call),
        patch("rollup.group_summarize._store_cached", side_effect=fail_store),
    ):
        new_dated, _, meta = apply_group_summaries(
            {"tech": (g,)}, (), _config(), MagicMock(), max_calls=8
        )
    assert new_dated["tech"][0].group_summary == "Generated group summary blurb."
    assert meta.cache_write_errors == 1
    assert meta.degraded is True


def test_max_output_chars_constant_is_group_specific() -> None:
    assert GROUP_SUMMARY_MAX_OUTPUT_CHARS == 1200
    assert GROUP_SUMMARY_MAX_OUTPUT_CHARS < 16_000
