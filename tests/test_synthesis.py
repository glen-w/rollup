"""Deterministic glance and LLM narrative parsing for briefing synthesis."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from rollup.classify import classify_message
from rollup.filter import make_digest_entry
from rollup.models import (
    BriefingSynthesis,
    CoverageCluster,
    DigestReport,
    DigestStats,
    ParsedMessage,
)
from rollup.parse import compute_content_hash
from rollup.synthesis import apply_synthesis, build_glance, parse_narrative


def _entry(key: str, subject: str):
    body = subject
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
        read_time_minutes=1,
        preview=body,
        parse_warnings=(),
        source_key="src:a",
    )
    return make_digest_entry(classify_message(parsed), no_ollama=True)


def _report(*entries, clusters=()):
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
        dated_by_folder={"tech": entries},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=len(entries),
            dated_included=len(entries),
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=len(entries),
        ),
        coverage_clusters=clusters,
    )


def test_glance_uses_high_relevance_and_clusters() -> None:
    high = replace(
        _entry("k1", "Local-first evals shipped"),
        attention_band="high_relevance",
    )
    other = _entry("k2", "Unrelated")
    cluster = CoverageCluster(
        cluster_id="c1",
        theme_label="example.com",
        member_keys=("k1", "k2"),
        representative_key="k1",
        source_count=2,
    )
    glance = build_glance(_report(high, other, clusters=(cluster,)))
    assert glance is not None
    assert glance.source == "glance"
    assert any("Local-first" in b for b in glance.bullets)
    assert any("Covered 2 ways" in b for b in glance.bullets)


def test_empty_window_has_no_glance() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
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
        ),
    )
    assert build_glance(report) is None
    assert apply_synthesis(report).briefing_synthesis is None


def test_llm_narrative_replaces_glance_when_complete_returns_json() -> None:
    entry = replace(_entry("k1", "Hello"), attention_band="high_relevance")
    report = _report(entry)

    def complete(_prompt: str) -> str:
        return json_blob()

    out = apply_synthesis(report, allow_llm=True, complete=complete)
    assert out.briefing_synthesis is not None
    assert out.briefing_synthesis.source == "ollama"
    assert out.briefing_synthesis.headline == "What mattered"
    assert "Stay focused" in out.briefing_synthesis.bullets


def json_blob() -> str:
    return (
        '{"headline":"What mattered","bullets":["Stay focused"],'
        '"what_to_skip":["Gossip"]}'
    )


def test_no_llm_path_never_calls_complete() -> None:
    called = {"n": 0}

    def complete(_prompt: str) -> str:
        called["n"] += 1
        return json_blob()

    entry = _entry("k1", "Hello")
    apply_synthesis(_report(entry), allow_llm=False, complete=complete)
    assert called["n"] == 0
    assert parse_narrative("not json") is None
    assert parse_narrative(json_blob()) == BriefingSynthesis(
        headline="What mattered",
        bullets=("Stay focused",),
        what_to_skip=("Gossip",),
        source="ollama",
    )
