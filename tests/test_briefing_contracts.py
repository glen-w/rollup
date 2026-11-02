"""Briefing-layer contracts: types, inclusion flattening, JSON/manifest keys."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from rollup.addons.json.serialize import SCHEMA_VERSION, report_to_dict
from rollup.briefing import (
    included_message_keys,
    inclusion_preserved,
    iter_digest_entries,
    report_included_message_keys,
)
from rollup.classify import classify_message
from rollup.filter import make_digest_entry
from rollup.manifest import filter_allowlisted
from rollup.models import (
    AttentionMetadata,
    BriefingSynthesis,
    CoverageCluster,
    DigestEntry,
    DigestGroup,
    DigestReport,
    DigestStats,
    ParsedMessage,
)
from rollup.parse import compute_content_hash


def _entry(key: str, subject: str = "Subject") -> DigestEntry:
    body = f"Body for {subject}"
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
    )
    return make_digest_entry(classify_message(parsed), no_ollama=True)


def _empty_stats() -> DigestStats:
    return DigestStats(
        folders_scanned=1,
        messages_parsed=3,
        dated_included=3,
        undated_needing_review=0,
        skipped_outside_window=0,
        skipped_seen_undated=0,
        deduped_messages=0,
        parse_errors=0,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=3,
    )


def _report(dated, undated=()) -> DigestReport:
    now = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
        dated_by_folder=dated,
        undated=undated,
        stats=_empty_stats(),
    )


def test_digest_entry_attention_defaults() -> None:
    entry = _entry("k1")
    assert entry.attention_band is None
    assert entry.attention_score is None
    assert entry.attention_reasons == ()
    assert entry.coverage_cluster_id is None


def test_digest_report_briefing_defaults() -> None:
    report = _report({})
    assert report.attention_metadata is None
    assert report.coverage_clusters == ()
    assert report.briefing_synthesis is None


def test_inclusion_flattens_groups_and_undated() -> None:
    standalone = _entry("k-standalone")
    grouped = DigestGroup(
        group_id="g1",
        group_type="notification_stream",
        display_name="Updates",
        sender_normalized="a@example.com",
        folder_name="tech",
        entries=(_entry("k-g1"), _entry("k-g2")),
    )
    undated = _entry("k-undated")
    report = _report({"tech": (grouped, standalone)}, undated=(undated,))
    assert included_message_keys((grouped, standalone)) == (
        "k-g1",
        "k-g2",
        "k-standalone",
    )
    assert [e.classified.parsed.message_key for e in iter_digest_entries((grouped,))] == [
        "k-g1",
        "k-g2",
    ]
    assert report_included_message_keys(report) == frozenset(
        {"k-g1", "k-g2", "k-standalone", "k-undated"}
    )
    assert inclusion_preserved(report, report)


def test_json_emits_empty_briefing_fields() -> None:
    entry = _entry("k1")
    report = _report({"tech": (entry,)})
    payload = report_to_dict(report, 8)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["attention_metadata"] is None
    assert payload["coverage_clusters"] == []
    assert payload["briefing_synthesis"] is None
    card = payload["dated_by_folder"]["tech"][0]
    assert card["attention_band"] is None
    assert card["attention_score"] is None
    assert card["attention_reasons"] == []
    assert card["coverage_cluster_id"] is None
    assert "body_text" not in card
    assert "body_html" not in card


def test_json_round_trips_populated_briefing_fields() -> None:
    entry = replace(
        _entry("k-rep"),
        attention_band="high_relevance",
        attention_score=0.91,
        attention_reasons=("interest-match",),
        coverage_cluster_id="c1",
    )
    report = DigestReport(
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        lookback_days=7,
        window_start=datetime(2026, 7, 2, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 2, tzinfo=timezone.utc),
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=_empty_stats(),
        attention_metadata=AttentionMetadata(
            profile_present=True,
            band_counts=(("high_relevance", 1),),
        ),
        coverage_clusters=(
            CoverageCluster(
                cluster_id="c1",
                theme_label="example.com",
                member_keys=("k-rep", "k-other"),
                representative_key="k-rep",
                source_count=2,
            ),
        ),
        briefing_synthesis=BriefingSynthesis(
            headline="What mattered",
            bullets=("One item.",),
            source="glance",
        ),
    )
    payload = report_to_dict(report, 8)
    assert payload["attention_metadata"]["profile_present"] is True
    assert payload["coverage_clusters"][0]["representative_key"] == "k-rep"
    assert payload["briefing_synthesis"]["source"] == "glance"
    card = payload["dated_by_folder"]["tech"][0]
    assert card["attention_band"] == "high_relevance"
    assert card["coverage_cluster_id"] == "c1"


def test_manifest_allowlist_keeps_reserved_briefing_keys() -> None:
    filtered = filter_allowlisted(
        {
            "schema_version": 1,
            "run_id": "11111111-2222-3333-4444-555555555555",
            "started_at": "2026-07-10T09:00:00+00:00",
            "completed_at": "2026-07-10T09:01:00+00:00",
            "status": "success",
            "attention": {"profile_present": True},
            "coverage": {"clusters": 1},
            "briefing_synthesis": {"source": "glance"},
            "body_text": "secret",
            "subject": "secret subject",
        }
    )
    assert "attention" in filtered
    assert "coverage" in filtered
    assert "briefing_synthesis" in filtered
    assert "body_text" not in filtered
    assert "subject" not in filtered
