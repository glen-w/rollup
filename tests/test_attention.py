"""Attention ranking: inclusion invariant, bands, config, LLM parse."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.attention.annotate import (
    annotate_items,
    apply_band_overrides,
    high_relevance_entries,
    sort_items_by_attention,
)
from rollup.attention.config import InterestConfig, parse_interest_config
from rollup.attention.llm import parse_band_overrides
from rollup.attention.score import AttentionSignals, score_entry
from rollup.attention.stage import apply_attention
from rollup.briefing import report_included_message_keys
from rollup.classify import classify_message
from rollup.filter import make_digest_entry
from rollup.models import DigestReport, DigestStats, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.render import render_html, render_markdown
from rollup.user_config import UserConfigError, parse_toml_dict


def _entry(key: str, subject: str, preview: str = "", source_key: str = "src:a"):
    body = preview or subject
    parsed = ParsedMessage(
        message_key=key,
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject=subject,
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime(2026, 7, 2, tzinfo=timezone.utc),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=3 if "long" in key else 1,
        preview=body,
        parse_warnings=(),
        source_key=source_key,
    )
    return make_digest_entry(classify_message(parsed), no_ollama=True)


def test_empty_signals_are_worth_scanning() -> None:
    entry = _entry("k1", "Random subject about nothing")
    scored = score_entry(
        entry, profile_text="", signals=AttentionSignals()
    )
    assert scored.band == "worth_scanning"
    assert scored.reasons == ()


def test_interest_match_raises_band() -> None:
    entry = _entry("k1", "Local-first LLM evals for shipping software")
    scored = score_entry(
        entry,
        profile_text="I care about local-first tools, LLM evals, and shipping software.",
        signals=AttentionSignals(),
    )
    assert scored.band in {"high_relevance", "worth_scanning"}
    assert "interest-match" in scored.reasons


def test_dismissed_can_skip_but_stays_included() -> None:
    a = _entry("k-keep", "Local-first LLM evals")
    b = _entry("k-skip", "Celebrity gossip roundup")
    signals = AttentionSignals(dismissed=frozenset({"k-skip"}))
    annotated = annotate_items((a, b), profile_text="", signals=signals)
    skip = next(e for e in annotated if e.classified.parsed.message_key == "k-skip")
    assert skip.attention_band == "safely_skip"
    assert "dismissed" in skip.attention_reasons
    assert {e.classified.parsed.message_key for e in annotated} == {"k-keep", "k-skip"}


def test_sort_puts_high_before_skip() -> None:
    high = replace(_entry("k-h", "x"), attention_band="high_relevance", attention_score=0.9)
    skip = replace(_entry("k-s", "y"), attention_band="safely_skip", attention_score=0.1)
    ordered = sort_items_by_attention((skip, high))
    assert [e.classified.parsed.message_key for e in ordered] == ["k-h", "k-s"]


def test_llm_overrides_only_reband() -> None:
    a = replace(_entry("k1", "A"), attention_band="worth_scanning")
    b = replace(_entry("k2", "B"), attention_band="worth_scanning")
    out = apply_band_overrides(
        (a, b), {"k1": "high_relevance", "unknown": "safely_skip"}
    )
    by_key = {e.classified.parsed.message_key: e for e in out}
    assert by_key["k1"].attention_band == "high_relevance"
    assert "llm-reband" in by_key["k1"].attention_reasons
    assert by_key["k2"].attention_band == "worth_scanning"
    assert parse_band_overrides('{"items":[{"message_key":"k1","band":"background"}]}')[
        "k1"
    ] == "background"
    assert parse_band_overrides("not json") == {}


def test_interest_toml_and_file(tmp_path: Path) -> None:
    parsed = parse_toml_dict(
        {"interest": {"profile": "local-first tools"}},
        path=tmp_path / "c.toml",
    )
    assert parsed.interest.profile == "local-first tools"
    with pytest.raises(UserConfigError, match="unknown"):
        parse_toml_dict({"interest": {"weights": 1}}, path=tmp_path / "c.toml")
    cfg = parse_interest_config(None, path=tmp_path / "c.toml")
    assert cfg.present is False


def test_render_start_here_and_skip_class() -> None:
    high = replace(
        _entry("k-h", "Must read local-first"),
        attention_band="high_relevance",
        summary="A summary.",
        summary_source="preview_fallback",
    )
    skip = replace(
        _entry("k-s", "Skip me"),
        attention_band="safely_skip",
        summary="Nope.",
        summary_source="preview_fallback",
    )
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
        dated_by_folder={"tech": (skip, high)},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=2,
            dated_included=2,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=2,
        ),
    )
    md = render_markdown(report, 8)
    html = render_html(report, 8)
    assert "## Start here" in md
    assert "Must read local-first" in md
    assert "Skip me" in md
    assert report_included_message_keys(report) == frozenset({"k-h", "k-s"})
    assert "Start here" in html
    assert "safely-skip" in html
    assert high_relevance_entries(report)[0].classified.parsed.message_key == "k-h"


def test_no_attention_complete_is_not_called() -> None:
    called = {"n": 0}

    def complete(_prompt: str) -> str:
        called["n"] += 1
        return '{"items":[]}'

    apply_attention(
        (_entry("k1", "Hello"),),
        (),
        interest=InterestConfig(profile="hello world tools"),
        conn=None,
        allow_llm=False,
        complete=complete,
    )
    assert called["n"] == 0
