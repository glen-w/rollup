"""Grouping heuristics and publication tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from rollup.classify import classify_message
from rollup.filter import make_digest_entry
from rollup.grouping import (
    apply_grouping,
    is_long_form_standalone,
    normalize_email,
    normalize_subject_family,
)
from rollup.models import (
    ClassifiedMessage,
    DigestEntry,
    DigestGroup,
    ParsedMessage,
)
from rollup.publication import publish_latest_outputs, should_update_latest
from rollup.run_options import GroupingConfig


def _entry(
    *,
    subject: str,
    sender: str = "alerts@github.com",
    folder: str = "tech",
    body: str = "short update body",
    days_ago: int = 1,
    newsletter_type: str | None = None,
) -> DigestEntry:
    now = datetime.now(timezone.utc)
    parsed = ParsedMessage(
        message_key=f"mid:{subject}-{days_ago}",
        content_hash="x" * 64,
        folder_name=folder,
        relative_folder_path=folder,
        subject=subject,
        sender=sender,
        date_raw="",
        date_parsed=now - timedelta(days=days_ago),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=max(1, len(body.split()) // 200),
        preview=body[:80],
        parse_warnings=(),
    )
    if newsletter_type:
        classified = ClassifiedMessage(
            parsed=parsed,
            newsletter_type=newsletter_type,  # type: ignore[arg-type]
            classification_scores=((newsletter_type, 1.0),),
        )
        return DigestEntry(
            classified=classified, summary=parsed.preview, summary_source="preview_fallback"
        )
    return make_digest_entry(classify_message(parsed), no_ollama=True)


def test_normalize_helpers() -> None:
    assert normalize_email("Name <Alerts@GitHub.COM>") == "alerts@github.com"
    assert "issue" not in normalize_subject_family("Re: Daily Brief Issue #12")


def test_long_form_essay_never_grouped() -> None:
    essay = _entry(
        subject="Long essay",
        body=("word " * 1200).strip(),
        newsletter_type="essay",
    )
    assert is_long_form_standalone(essay)
    result = apply_grouping(
        (essay, *[
            _entry(subject=f"n{i}", body="tiny", newsletter_type="short_update", days_ago=i)
            for i in range(3)
        ]),
        (),
        GroupingConfig(enabled=True, min_group_size=3),
    )
    assert any(isinstance(i, DigestEntry) and i is essay or (
        isinstance(i, DigestEntry) and i.classified.parsed.subject == "Long essay"
    ) for i in result.dated_items)


def test_notification_stream_groups() -> None:
    entries = tuple(
        _entry(
            subject=f"Build failed #{i}",
            body="ci failed briefly",
            newsletter_type="short_update",
            days_ago=i,
        )
        for i in range(5)
    )
    result = apply_grouping(entries, (), GroupingConfig(enabled=True, min_group_size=3))
    groups = [i for i in result.dated_items if isinstance(i, DigestGroup)]
    assert len(groups) == 1
    assert groups[0].group_type == "notification_stream"
    assert len(groups[0].entries) == 5
    assert any(d.reason_code == "FORMED_NOTIFICATION_STREAM" for d in result.reason_codes)


def test_daily_editions_group() -> None:
    entries = tuple(
        _entry(
            subject=f"The Daily — 2026-07-0{i} edition",
            sender="daily@news.example",
            body="section one\n\nsection two\n\n" + ("more words " * 40),
            newsletter_type="multi_section_digest",
            days_ago=i,
        )
        for i in range(1, 6)
    )
    result = apply_grouping(entries, (), GroupingConfig(enabled=True, min_group_size=3))
    groups = [i for i in result.dated_items if isinstance(i, DigestGroup)]
    # May form daily_editions if subject family + edition regex match.
    assert groups
    assert groups[0].group_type in {"daily_editions", "notification_stream"}


def test_below_min_size_stays_standalone() -> None:
    entries = tuple(
        _entry(subject=f"n{i}", body="tiny", newsletter_type="short_update", days_ago=i)
        for i in range(2)
    )
    result = apply_grouping(entries, (), GroupingConfig(enabled=True, min_group_size=3))
    assert all(isinstance(i, DigestEntry) for i in result.dated_items)
    assert any(d.reason_code == "BELOW_MIN_SIZE" for d in result.reason_codes)


def test_publish_latest_skips_partial_by_default(tmp_path: Path) -> None:
    md = tmp_path / "digest.md"
    html = tmp_path / "digest.html"
    md.write_text("# hi\n", encoding="utf-8")
    html.write_text("<html></html>", encoding="utf-8")
    assert should_update_latest("partial", publish_latest=True, allow_partial_latest=False) is False
    result = publish_latest_outputs(
        output_dir=tmp_path,
        md_path=md,
        html_path=html,
        run_status="partial",
        publish_latest=True,
        allow_partial_latest=False,
    )
    assert result.latest_outputs_updated is False
    assert not (tmp_path / "latest.md").exists()

    result2 = publish_latest_outputs(
        output_dir=tmp_path,
        md_path=md,
        html_path=html,
        run_status="success",
        publish_latest=True,
    )
    assert result2.latest_outputs_updated is True
    assert (tmp_path / "latest.md").read_text() == "# hi\n"
