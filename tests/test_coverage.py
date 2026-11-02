"""Cross-source coverage clustering: overlap, inclusion, --no-coverage."""

from __future__ import annotations

from datetime import datetime, timezone

from rollup.briefing import included_message_keys
from rollup.classify import classify_message
from rollup.coverage.cluster import apply_clusters_to_items, cluster_entries
from rollup.coverage.normalize import canonical_url, normalize_title
from rollup.coverage.stage import apply_coverage
from rollup.filter import make_digest_entry
from rollup.models import LinkItem, ParsedMessage
from rollup.parse import compute_content_hash


def _entry(key: str, subject: str, href: str, source_key: str, folder: str = "tech"):
    body = f"{subject} {href}"
    parsed = ParsedMessage(
        message_key=key,
        content_hash=compute_content_hash(body),
        folder_name=folder,
        relative_folder_path=folder,
        subject=subject,
        sender=f"{source_key}@example.com",
        date_raw="",
        date_parsed=datetime(2026, 7, 2, tzinfo=timezone.utc),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=1,
        html_section_break_count=0,
        links=(href,),
        link_items=(LinkItem(href=href, text=subject, context=None, source_index=0),),
        read_time_minutes=4,
        preview=body,
        parse_warnings=(),
        source_key=source_key,
    )
    return make_digest_entry(classify_message(parsed), no_ollama=True)


def test_canonical_url_strips_tracking() -> None:
    assert canonical_url("https://Example.com/Post?utm_source=x&id=1") == (
        "https://example.com/Post?id=1"
    )


def test_normalize_title_strips_dates() -> None:
    assert "2026" not in normalize_title("Weekly Newsletter #12 — 2 July 2026")


def test_same_url_clusters_across_sources() -> None:
    a = _entry("mail-1", "Local-first tools ship", "https://example.com/a", "mail:src")
    b = _entry(
        "li-1",
        "Local-first tools ship",
        "https://example.com/a?utm_campaign=x",
        "linkedin:src",
        folder="linkedin",
    )
    c = _entry("other", "Unrelated weather", "https://other.test/z", "mail:other")
    clusters = cluster_entries((a, b, c))
    assert len(clusters) == 1
    assert set(clusters[0].member_keys) == {"mail-1", "li-1"}
    assert clusters[0].source_count == 2
    items = apply_clusters_to_items((a, b, c), clusters)
    assert included_message_keys((a, b, c)) == included_message_keys(items)
    clustered = [e for e in items if e.coverage_cluster_id]
    assert len(clustered) == 2


def test_title_jaccard_clusters_without_shared_url_when_high() -> None:
    a = _entry("a", "Shipping local first LLM evals today", "https://one.test/1", "s1")
    b = _entry("b", "Shipping local first LLM evals today", "https://two.test/2", "s2")
    clusters = cluster_entries((a, b))
    assert len(clusters) == 1


def test_apply_coverage_preserves_keys() -> None:
    a = _entry("a", "Alpha", "https://example.com/x", "s1")
    b = _entry("b", "Beta", "https://example.com/y", "s2")
    dated, undated, clusters = apply_coverage((a, b), ())
    assert included_message_keys(dated) == ("a", "b")
    assert undated == ()
    assert isinstance(clusters, tuple)
